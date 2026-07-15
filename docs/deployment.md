# EurekaLoop Deployment Notes

## Web Deployment

EurekaLoop is currently a static Vite web app.

```bash
npm install
npm run build
```

Upload `dist/` to Vercel, Netlify, or GitHub Pages.

For GitHub Pages, set the repository Pages source to a static deployment workflow or publish `dist/` through an action.

## Backend Integration

Set:

```bash
VITE_API_BASE_URL=http://localhost:8000
VITE_ENABLE_MOCK=false
```

Then replace the mock runner with a real API client:

- task creation
- task start
- stage detail fetch
- inline Review Gate action submit
- module revision and rerun submit
- artifact list fetch
- SSE event stream

## Desktop App Path

Current recommendation:

1. Finish and test the browser experience first.
2. Connect the real backend API, Artifact Service, and event stream.
3. Only then decide whether the team still needs a desktop app.
4. If yes, wrap the same React/Vite frontend with Tauri for a lightweight shell.
5. Let the Tauri backend call the same HTTP backend or local orchestrator.

Electron is also possible, but Tauri is lighter for a demo app.
