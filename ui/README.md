# DANGLE frontend

The frontend is intentionally dependency-free and local. `index.html`, `styles.css`, and `app.js` implement the DANGLE identity, boot reveal, lightweight canvas data field, home-to-chat transition, history sidebar, and compact controls.

The supplied boot image is `ui/assets/old-logo.png`. It appears on the white opening frame, then is visually destroyed before DANGLE resolves. Replace that file with a newer transparent PNG if needed; no code change is required.

## Existing functionality mapping

- Model, context, thinking, library search, retrieval count, temperature, top-p, and output limits: wrench panel.
- Service status, update, restart, debug, clear conversation, system prompt, model storage, and privacy/path information: top-right gear panel.
- Local chat history, new/open/rename/delete conversation: left archive sidebar.
- Source PDFs, retrieval reason, context accounting, performance, and thinking text: expandable message metadata.

The animation uses one requestAnimationFrame loop with a small particle budget and adaptive quality. Its target intensity is `HOME_IDLE=.2`, `CHAT_IDLE=.5`, and `GENERATING=1`; it pauses while the document is hidden.
