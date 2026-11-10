# README image provenance

`group-flow.svg` is an original bilingual **illustration**, not a screenshot of Telegram or a live delivery receipt. It explains mention/reply → shared group context → explicitly enabled scoped read-only GitHub/Linear → group answer, plus the separately budgeted daily digest. No real group, user, credentials or service data appears.

Source: existing README behavior, `templates/hermes/config.yaml` and `templates/cron/twitter_digest.md`. Recreate with `node docs/images/draw-flow.mjs`. Both README languages use the same bilingual image, descriptive localized alt and an explicit illustration caption. GitHub/Linear remain opt-in; the drawing is not evidence of a live integration or deployment.

No terminal image was added: a matching Hermes v0.23.0 virtual environment was unavailable in the task's independent workspace. We did not invent offline initializer output or run paid model/Apify/Telegram calls for a picture.

`baseline.json` snapshots the README bytes before insertion; `illustrate.json` records the image source and exact blocks. With the v0.3 skill, copy `baseline.json` to WORK/illustrate-baseline.json and `illustrate.json` / `brief.json` to WORK, then run `illustrate verify --repo . --work WORK`. Original bytes, H2s, badges and commands must match after removing declared blocks. The complete real install block (not the earlier offline demo) is already below the desktop fold; the draft PR discloses this hard failure. Preview screenshots remain outside the repository.
