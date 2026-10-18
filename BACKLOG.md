# Backlog

Couch Check rates intimate content 0–10 per title. Done so far: the main-screen chip, the in-app breakdown with summary, the stream-list row, the web breakdown page, live AI scores with keyword estimates as a fallback, an AI on/off setting, and saved scores in `data/scores.json` that survive redeploys.

## Now

1. **Public instance.** Host one copy for everyone once BeamUp (or another free host) can run it, with `PG_SCENE_TEXT=off`.
2. **Pre-score popular titles:** score a list of popular movies and shows, so most titles say `AI` from day one and the free AI quota goes further.

## Next

1. **Episode-level scores for shows.** A show-wide score is effectively the worst episode. When an episode is picked, use that episode's own IMDb guide if it exists; otherwise fall back to the show's and label it "show-wide".
2. **Scene timing.** Pull rough timing out of flagged items ("around 45 minutes in", "opening scene") so scenes can be skipped.
3. **Personal cut-offs.** Settings with limits (e.g. Family ≤ 3, Us ≤ 5), shown as `✅ Family · ✅ Us` / `❌ Family · ✅ Us`.
4. **Rubric tuning.** Keep comparing AI scores against the scene text, and tighten the rules wherever they're misread (the rubric is versioned; AI scores refresh on change). Run `tools/check_rubric.py` after every change and add titles to `data/reference.json` as they're settled.

## Later

- **Family-safe catalogs.** A "Popular & Family-safe" row on the Stremio home screen, built from scored titles.
- **Compact AI output.** A shorter answer format (`[[4,1,0],…]`) would cut AI output tokens by about three-quarters.

## Decided

- Only intimate content is scored. Violence, profanity, drugs and frightening scenes are ignored.
- No separate flag for assault scenes; they're scored on the 0–10 ladder like any other scene.
- Edge cases are settled in the rubric (v5.2) in `pg/rubric.py`.
- Live AI scores come from an OpenAI-compatible API (Groq's free tier). The key lives only in the server's environment. IMDb's text is never published, only fetched live per title.
- Stream rows can't open in-app screens (Stremio ignores in-app links there), so the row opens the web page and the chip opens the in-app breakdown.
- The rubric stays public; summaries describe content without judging who it's suitable for.
