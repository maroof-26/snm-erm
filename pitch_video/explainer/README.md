# Explainer video

`python3 explainer.py --reset` re-records everything (wipes the demo client's data first) and writes `../urdu_full_walkthrough.mp4`.
Edit the Urdu sentences and on-screen actions in `build_scenes()`; set `EXPLAINER_WORK` to a scratch folder for the intermediate files.
Needs the dev server on :8010, the `pitch` login, edge-tts, ffmpeg, pdftoppm, Playwright + Chrome.
