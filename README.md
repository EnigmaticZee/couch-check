<p align="center">
  <img src="docs/showcase.svg" alt="Couch Check: know what to expect before you press play with family. Shows the score chip on a title's main screen, the row in the stream list, and a score card." width="100%">
</p>

<p align="center">
  <img alt="Stremio addon" src="https://img.shields.io/badge/Stremio-addon-7B5BF5">
  <img alt="Rubric: beta" src="https://img.shields.io/badge/rubric-beta-F0BF7E">
  <img alt="Python 3.9+" src="https://img.shields.io/badge/python-3.9%2B-3776AB">
  <img alt="No dependencies" src="https://img.shields.io/badge/dependencies-none-2F9A5A">
  <img alt="MIT license" src="https://img.shields.io/badge/license-MIT-lightgrey">
</p>

> [!NOTE]
> **Couch Check is in beta.** The scoring rubric is still being tuned, so a score can occasionally be off. Every score links to the scene-by-scene list it came from, so you can always check for yourself.

## What it does

Couch Check reads each title's IMDb parents guide and turns its intimate-content section into one clear number, so you can decide in seconds whether something is right for movie night.

- **One score, 0–10.** The score is the most intense moment in the title, on a fixed ladder where every number means something specific.
- **On the title's main screen.** A *Couch Check* chip sits right under the rating, before you even pick an episode.
- **A breakdown inside Stremio.** Tap the chip for a short summary and every flagged scene, grouped by level, with spoilers hidden.
- **In every stream list.** A row at the top of each episode's and movie's sources.
- **AI score or quick estimate.** An AI reads every scene description. Until it has, a keyword estimate fills in, clearly labelled.

## Install

A public instance is coming soon. Until then, [run your own](#run-your-own); it takes a couple of minutes.

To get the chip on the main screen, Couch Check has to sit **above Cinemeta** in your addon list. The Stremio app can't reorder addons, so use a community addon manager (search "Stremio Addon Manager"), drag Couch Check above Cinemeta, sync, and restart Stremio. The stream-list row works without this step.

Prefer no AI? The configure page has a **Use AI scores** switch. With it off, you only see keyword estimates.

## The scale

| Score | Name |
|---|---|
| 🟢 0 | None |
| 🟢 1 | Affection |
| 🟢 2 | Romance |
| 🟡 3 | Suggestive |
| 🟡 4 | Implied |
| 🟠 5 | Brief partial n*dity |
| 🟠 6 | S*x scene, covered |
| 🔴 7 | Topless |
| 🔴 8 | S*x scene with n*dity |
| 🔴 9 | Full frontal |
| 🔴 10 | Graphic |

Next to the score you'll also see how many **scenes** are at level 4 or above, how many are **prolonged**, whether a show's score is **show-wide**, and **low confidence** when IMDb has very little to go on.


## AI score vs estimate

Every score is labelled with where it came from:

- **`AI`:** an AI model read each scene description and placed it on the scale. It understands context: a *joke* about something isn't the same as seeing it, and something *implied* isn't the same as something shown.
- **`Est.`:** a quick keyword estimate, shown until a title has been AI-scored (or always, if you turn AI off). It's rougher and can be fooled by wording.

The first time anyone opens a title, the server saves the estimate immediately and asks the AI, waiting up to 5 seconds. Normally the AI answers in time, so the first view already says `AI`. Either way, the result is cached and every later view is instant.

## Run your own

Couch Check needs only Python 3.9+, with no packages to install.

```bash
git clone https://github.com/EnigmaticZee/couch-check.git
cd couch-check
python3 server.py
```

That serves `http://localhost:7001`, which is handy for the breakdown pages in a browser. **Stremio only installs addons over HTTPS**, so for Stremio either host it (see below) or use the included Cloudflare quick tunnel:

```bash
mkdir -p bin && curl -sL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-arm64.tgz | tar -xz -C bin
./start.sh
```

`start.sh` prints a `https://….trycloudflare.com/configure` address. Open it and click **Install in Stremio**. The address changes each time you start it.

### AI scores

Copy `.env.example` to `.env` and add a key for any OpenAI-compatible API. The template is set up for [Groq's](https://console.groq.com) free tier. Without a key, everything works with estimates.

> [!IMPORTANT]
> Never commit `.env` or paste the key anywhere public. It's git-ignored, and the key is only read from the environment: it never appears in the code, the repo or any response. Use a dedicated account with no payment method, and revoke the key in your provider's console if it ever leaks.

### Hosting

Any host that runs Python or Docker and gives an HTTPS address works. A [`Dockerfile`](Dockerfile) is included, and it keeps the cache on a `/data` volume. Set your AI key as a secret environment variable on the host, and set `PG_PUBLIC_URL` if the host's address isn't detected automatically.

### Settings

| Variable | Default | |
|---|---|---|
| `PG_AI_BASE_URL`, `PG_AI_API_KEY`, `PG_AI_MODEL` | — | OpenAI-compatible API for AI scores (e.g. Groq) |
| `PG_AI_MIN_INTERVAL` | `1.5` | Seconds between AI calls |
| `PG_AI_DAILY_LIMIT` | `500` | Max AI calls per day; beyond that, new titles get estimates |
| `PG_AI_REASONING_EFFORT` | `low` for gpt-oss | Reasoning effort for reasoning models |
| `PG_CLASSIFIER` | — | `keywords` turns AI scoring off server-wide |
| `PG_SCENE_TEXT` | `on` | `off` shows scene counts per level with a link to IMDb instead of IMDb's scene descriptions. Recommended for public instances |
| `PORT` | `7001` | 7000 is taken by macOS AirPlay Receiver |
| `PG_PUBLIC_URL` | from request | Public base URL behind a proxy or tunnel |
| `PG_DB` | `./cache.sqlite3` | Cache location |
| `PG_SCORES` | `./data/scores.json` | Offline scores file |

### Offline scores

[`data/scores.json`](data/scores.json) can hold AI scores made offline. They always take precedence over live ones. The file contains only IMDb item IDs and our levels, never IMDb's text.

1. `python3 tools/export_pending.py [imdb ids…]` writes the titles to score, with their IMDb text, to `work/` (git-ignored).
2. Score them following `work/rubric.txt`, and save the result as `work/scored.json`.
3. `python3 tools/import_scores.py` validates the result and merges it into `data/scores.json`.

## About the data

IMDb has no public API for parents guides. Couch Check uses the same endpoint IMDb's website uses, fetches only titles someone opens, and waits at least a second between requests. Finished titles (shows that have ended, movies more than a year old) are cached permanently; ongoing shows and recent movies are refreshed every three weeks. To force a refresh, run `python3 tools/refresh.py <imdb ids…>` on the server.

Scene descriptions shown in the breakdowns are written by IMDb's contributors; Couch Check only adds the levels and summaries. If you run a public instance, set `PG_SCENE_TEXT=off` so breakdowns link to IMDb for the descriptions instead of showing them. IMDb's terms allow only limited personal, non-commercial use of its data, so please run Couch Check with that in mind.

## Roadmap

Episode-level scores, scene timing and personal cut-offs ("✅ Family · ✅ Us") are next. See [BACKLOG.md](BACKLOG.md).

## License

[MIT](LICENSE)
