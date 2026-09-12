# Current product tour

These screenshots and the captioned walkthrough show the real Throughline UI with the isolated fictional demo corpus. No model requests or agent launches occur. The walkthrough demonstrates navigation and template inspection, not autonomous completion of projects.

The tour has burned-in English captions and a separate WebVTT track. The GIF is a short, silent animated preview; the MP4 contains the full walkthrough. The mobile screenshot uses a 390×844 viewport. `manifest.json` records capture metadata and browser errors.

## Reproduce

Create an isolated demo using [the demo guide](../DEMO.md), apply current migrations and build the frontend. Serve the fixture on port 8795. With Node, Playwright Chromium and ffmpeg installed:

```bash
npm --prefix web ci
(cd web && npx playwright install chromium)
npm --prefix web run build
THROUGHLINE_DEMO_URL=http://127.0.0.1:8795 node web/scripts/record-product-tour.mjs
ffmpeg -y -i docs/media/tour.webm -vf 'scale=1280:-2' -c:v libx264 -preset slow -crf 31 -pix_fmt yuv420p -movflags +faststart -an docs/media/throughline-tour.mp4
ffmpeg -y -i docs/media/throughline-tour.mp4 -t 18 -vf 'fps=4,scale=640:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=64[p];[b][p]paletteuse=dither=bayer' docs/media/throughline-tour.gif
```

The template-focused `templates-tour.mp4` is a chapter excerpt from the same recording, starting at the fourth caption in `manifest.json` (12.678 seconds in this capture). `templates-tour.vtt` shifts those cues to the chapter timeline.

The source WebM is an intermediate and is not committed. Inspect the final video, image frames and captions before publishing. Demo guards check the expected corpus, but are not a security boundary; never record a production database.
