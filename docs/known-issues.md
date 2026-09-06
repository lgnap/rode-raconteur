# Problèmes connus

Relevés à la revue finale de la branche `feat/enregistreur-vocal` (6 septembre 2026),
arbitrés et laissés en l'état plutôt que corrigés. Aucun n'est bloquant ; chacun est
documenté avec ce qu'il coûte et ce qu'il faudrait faire.

## Bibliothèques CUDA et repli CPU

Sur cette machine, CUDA vient des paquets pip `nvidia-cublas-cu12` et
`nvidia-cudnn-cu12` plutôt que de la distribution, et leurs `.so` ne sont pas sur le
chemin de l'éditeur de liens. CTranslate2 ouvre `libcublas.so.12` par `dlopen` au
**premier encodage**, pas au chargement du modèle : un GPU visible et un modèle chargé
sans erreur ne garantissent donc rien.

`conteur/cuda.py` précharge ces bibliothèques en `RTLD_GLOBAL` avant de construire le
modèle. `LD_LIBRARY_PATH` ne conviendrait pas : il est lu au démarrage du processus,
trop tard pour être corrigé depuis Python.

Si cuBLAS reste introuvable, le modèle est construit sur **CPU** en `int8` au lieu
d'échouer en pleine transcription. C'est plus lent — acceptable sur des fragments
courts, sensible sur un récit de plusieurs minutes. `chosen_device()` dit ce qui a été
retenu.

## Fermeture : deux attentes non bornées

`app.py` — `shutdown()` appelle `_finish_capture`, qui fait `self._capture.join()`
puis `_await_model()` → `self._model_loader.join()`, tous deux sans délai.

Si un `read` est bloqué sur un périphérique fantôme, ou si la première prise est
arrêtée avant la fin du chargement de `large-v3`, la fenêtre pend à la fermeture sans
retour visuel. Le drainage de la file, lui, est correctement borné à 30 s.

C'est un compromis assumé : borner ces attentes reviendrait à abandonner de l'audio,
ce qui viole la contrainte la plus dure du projet. Une correction acceptable serait un
délai généreux assorti d'un message expliquant l'attente, plutôt qu'un abandon
silencieux.

## Un échec PortAudio au lancement désarme le préchargement du modèle

`app.py` — `refresh_device()` est appelé avant `_start_model_loading()`. Si aucun
récepteur n'est présent **et** que la création du contexte PortAudio échoue, `_pa`
reste `None`, le préchargement est sauté, et un balayage réussi ultérieur ne le
réarme jamais. Le modèle repasse alors en chargement paresseux au premier arrêt,
figeant l'interface — exactement ce que le préchargement devait éviter.

Correction connue, une ligne : appeler `_start_model_loading()` depuis le chemin de
succès de `_rescan_devices`.

Cause de fond : le préchargement est conditionné à `self._pa is not None`, condition
qui sert en réalité de marqueur « fenêtre de test ». Un chargeur injecté explicitement
serait plus honnête et supprimerait ce couplage.

## Journal ALSA bruyant sur l'écran d'attente

Sans récepteur branché, le contexte PortAudio est détruit et recréé toutes les
2 secondes — c'est le prix d'une détection à chaud qui fonctionne réellement, PyAudio
n'offrant aucun rebalayage.

Le coût processeur est négligeable (17-18 ms par cycle, mesuré). Le vrai coût est
d'environ 200 lignes d'erreur ALSA par minute sur `stderr`, qui noieraient un
diagnostic réel. Un intervalle croissant après plusieurs balayages vides réglerait le
problème sans rien changer à la correction.

## Une garde non couverte par les tests

`job.py` — la garde `samples.size == 0` ne discrimine qu'en combinaison avec celle de
`signal.py`. Retirée seule, la suite reste verte, car `looks_like_timecode` rend
`False` sur un tableau vide et le chemin ordinaire aboutit déjà à `silence`.

Le comportement est correct et la paire est conjointement testée. La garde de `job.py`
existe pour épargner un appel Whisper sur un tampon vide, ce qu'une doublure de test
ne peut pas observer.

## Deux secondes de bouton trompeur

Après un débranchement détecté en cours de prise, le bouton affiche « Enregistrer » et
reste actif jusqu'au sondage suivant. Un clic dans cet intervalle est sans effet
nuisible : il désactive simplement le bouton.

## Mesure de la durée de parole

`speech_duration` somme les durées des **segments de transcription**, pas des régions
détectées par le VAD. Une pause interne à un segment est donc comptée comme de la
parole.

La règle de la spec — « un fragment de 40 s dont 6 de parole est traité comme court » —
tient pour les silences en tête et en queue, mais le seuil de 12 secondes est mesuré
sur une grandeur légèrement différente de celle que la spec décrit. Approximation
acceptable, à connaître avant d'ajuster le seuil.

## Ce que les tests ne couvrent pas

La suite compte 112 tests et ne touche ni matériel, ni GPU, ni réseau. Restent hors de
sa portée, et couverts uniquement par la vérification manuelle de la spec :

- le comportement réel du vumètre sur un signal du récepteur ;
- le garde-fou timecode sur un vrai canal LTC ;
- le débranchement physique du récepteur en cours de capture ;
- le dialogue de renommage lui-même (`QInputDialog`).
