# Contributing

The most useful contribution is **data about hardware I do not own**.

Everything in this repo was verified on one machine (Fedora 44, kernel 7.1.12,
KDE 6/Wayland) with one device family (Wireless PRO, firmware 1.0.2). The
device map is therefore thin. Extending it does not require you to write
anything:

```sh
./tools/rode-doctor.sh
```

Open an issue with that output and the model you have. The script is read-only
— it inspects sysfs, `/dev`, ALSA, PipeWire and any Bottles configuration, and
modifies nothing.

If your receiver produces audio, `--record` additionally captures four seconds
and reports the per-channel crest factor, which identifies a timecode channel:

```sh
./tools/rode-doctor.sh --record
```

## Corrections

If something here is wrong, say so plainly in an issue — including which part of
your setup differs from the one described. A claim that turns out to be
model-specific or distribution-specific is worth more as a correction than as a
silent workaround in your own notes.

## Style

- State what was verified and on what. Do not generalise beyond it.
- Prefer a command whose output the reader can compare against theirs.
- Keep the failure modes: a guide that only shows the clean path does not help
  anyone reproduce it.
