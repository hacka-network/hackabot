---
name: deploy
description: Deploy hackabot to production on Heroku
disable-model-invocation: true
allowed-tools: Bash
---

# Deploy to Heroku

1. Push to Heroku: `git push heroku main`
2. Check that the deploy succeeded by tailing the logs briefly: `heroku logs --tail` (stop after a few seconds)
