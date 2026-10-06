# CUAWright website (gh-pages)

This branch hosts the static landing page for CUAWright (formerly Webwright).

- `index.html` — CUAWright landing page
- `assets/cuawright/` — CUAWright logo, result figures, and demo video
- `webwright.html` — original Webwright landing page
- `showcase/` — demo videos and trace viewers
- `showcase/osworld/` — step-by-step viewer for ten OSWorld-V2 trajectories (GPT-5.5)

Serve locally:
```bash
python3 -m http.server 8765
open http://localhost:8765/
```

Do not merge this branch into `main`.
