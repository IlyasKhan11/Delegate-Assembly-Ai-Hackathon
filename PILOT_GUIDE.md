# Test Delegate with your friend

Your friend plays the **business representative in English**. You use the main workspace in **Português (Brasil)** and approve the English replies. You do not have to speak English yourself.

Start with chat. That verifies the useful part of the product: understanding the reply, translating it, respecting your limit, and asking before agreeing. Add speech after that works. Discord is for discussing the test; Delegate does not listen to Discord or dial telephone numbers.

## Start locally

```bash
./venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8000 --no-access-log
```

Open http://localhost:8000/app. Choose **Check setup**. This checks configuration without making paid AI calls or opening the microphone. Use **Explore a sample conversation** for a fixed rehearsal, or **Use a Portuguese test task** for a real AI conversation.

For a fresh Python 3.12 environment, install `requirements.lock`. The older `requirements.txt` additionally includes desktop microphone utilities, which the web app does not need.

## Share with a remote friend

A localhost invitation cannot work on your friend's computer. The workspace and invitation must use a publicly reachable HTTPS address.

1. In your untracked `.env`, set `DELEGATE_PUBLIC_MODE=true` and `DELEGATE_PILOT_PASSCODE` to a random passcode of at least 16 characters. Restart the app. Public mode intentionally requires HTTPS for sign-in cookies, so use the HTTPS address in the next step rather than localhost for the workspace.
2. Put an HTTPS reverse proxy or tunnel in front of port 8000. For a short test, install Cloudflare's official `cloudflared` and run:

   ```bash
   cloudflared tunnel --url http://localhost:8000
   ```

   Open the printed HTTPS address followed by `/app` and sign in. Keep both the app and tunnel running. [Cloudflare's Quick Tunnel instructions](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/) describe this temporary testing setup; it is not permanent hosting.
3. Choose the Portuguese test task, prepare it, and approve the opening.
4. Click **Invite participant** (localized in your selected language). Copy the invitation from this HTTPS workspace and send it to your friend yourself. They do not need the host passcode.
5. Keep your workspace open. Your friend responds in the separate participant page. Never send your controller session ID or the workspace passcode to business participants.

The ordinary room and live transcription do not need `PUBLIC_BASE_URL`. Only the optional AssemblyAI full voice-call bridge needs that setting. Use a stable HTTPS host for that bridge; Quick Tunnels do not support server-sent events, which the bridge uses. Provider availability and credits must be tested separately.

## A useful ten-minute test

| Person | Action | Expected result |
| --- | --- | --- |
| You | Select Português (Brasil), Assist mode, and a $20 maximum | Opening is drafted for your review |
| You | Approve the opening | Your friend sees the approved English text |
| Friend | Type “Early check-in costs thirty dollars.” | You see Portuguese translation and a price decision; $30 is not accepted |
| You | Choose a $20 counteroffer, inspect and approve it | Only the approved counteroffer reaches your friend |
| Friend | Type “Twenty dollars works. Shall I confirm the booking?” | Confirmation requires a separate approval |
| You | Edit or reject a draft | The old draft is never shared as an approved reply |
| You | End the conversation | The room closes and you can download the summary |

Then test audio: your friend presses **Enable agent audio here**, then **Dictate reply**, reviews it, and sends. If live transcription is configured, they can use **Start talking** instead. On your workspace, mute local playback so only your friend hears the agent. Wear headphones and mute Discord while speaking into Delegate. The host's **Listen** button listens to the host's own microphone.

Ask your friend where they hesitated, whether the responses made sense, and whether waiting for approval felt clear. Record translation errors, unexpected speech, and the time from sending a reply to seeing the draft. This is more useful than testing every audio mode at once.

## Hosting without Railway

### Blitz cloud

Deploy the repository using the root Dockerfile and keep `/data` as a persistent folder. Set `DELEGATE_DATABASE=/data/calls.sqlite3`, `DELEGATE_PUBLIC_MODE=true`, the provider keys, and a private `DELEGATE_PILOT_PASSCODE` of at least 16 characters. Leave the Docker start command in place; it honors the host's `PORT`.

The image uses UID/GID `1000:1000` and declares `/data` as a volume. Blitz's [Docker requirements](https://blitz.cloud/docs/deploy-docker-image/) describe running apps as UID/GID 1000; the data folder must be writable by that identity. An existing mount overrides the directory ownership built into the image. If startup reports `Database folder /data is not writable by user id 1000` (or, on older builds, `sqlite3.OperationalError: unable to open database file`), check the exact database setting and the mounted folder permissions in the runtime logs/settings. A mount previously created for UID 10001 needs its ownership corrected by the host; do not delete existing conversation data or move it into `/tmp` to hide this error.

After deploying, open `/app`, unlock the workspace, and run **Check setup**. Set `PUBLIC_BASE_URL` to the actual HTTPS origin if using the full voice-call bridge. Verify that a saved conversation remains available after a restart before relying on persistence. The free plan does not back up kept folders; download important summaries.

### Render free web service

The existing Dockerfile serves both the frontend and Python backend. You do not need a separate frontend host.

1. Push the latest app changes to your deployment repository so the English-default update is included.
2. In [Render](https://dashboard.render.com/), choose **New → Web Service**, connect `IlyasKhan11/Delegate-Assembly-Ai-Hackathon`, and select `main`.
3. Choose **Docker**, use the root `Dockerfile`, select the **Free** instance, and set the health-check path to `/api/health`. Keep one instance; the Docker command already uses one worker and honors Render's `PORT`.
4. Add `AIML_API_KEY`, `ASSEMBLYAI_API_KEY`, and a random `DELEGATE_PILOT_PASSCODE` of at least 16 characters in the service's secret environment settings. Do not upload or commit `.env`. The Dockerfile already enables `DELEGATE_PUBLIC_MODE=true`.
5. Deploy. Once the service reports **Live**, open its assigned HTTPS address followed by `/app`, unlock it with the pilot passcode, and select **Check setup**. Try the sample conversation first; real AI and speech features still require provider credits.
6. For the optional full live voice-call bridge, set `PUBLIC_BASE_URL` to the assigned HTTPS origin (without `/app`) and redeploy. Ordinary browser conversations and live transcription do not need this setting.

Render's documentation supports [free services without a payment method](https://render.com/docs/free), but that is not a guarantee that your account will avoid a card-verification request. If the dashboard requires a card, stop there; this project cannot bypass that requirement. The `render.yaml` Blueprint is also available for accounts that can use it.

Free services sleep after 15 minutes without inbound traffic and can take about a minute to wake. Saved SQLite conversations are lost on sleep, restart, or redeployment; download summaries you want to keep. The free allowance is 750 instance hours per workspace each month, with separate bandwidth and build limits. See [Render's current limits](https://render.com/docs/free).

### No-account temporary demo

If account verification blocks hosting, the Cloudflare Quick Tunnel described under **Share with a remote friend** gives you a temporary public HTTPS address without a hosting account. Your computer, app, and tunnel must remain running. This is useful for a demo but does not provide hosting while your laptop is off, a stable URL, or support for the full voice-call bridge's server-sent events. Keep public mode and the pilot passcode enabled before exposing the app.

## Keep it running

A container deployment is included:

```bash
docker compose up --build -d
docker compose ps
docker compose logs --tail=50 delegate
```

The container runs as an unprivileged user, stores conversations in the `conversations` named volume, and binds only to localhost. Put an HTTPS proxy in front of it. Configure the keys and pilot passcode in `.env`; secrets are excluded from the image. Do not run multiple workers or replicas: the current session coordinator is in one process. Restart without deleting the named volume to retain calls. Avoid logging request URLs in your proxy because invitation URLs are private capability links.

`/api/health` reports configuration booleans, not API keys. It is a liveness/configuration check, not proof of provider connectivity. The host gate uses a shared passcode and 12-hour HttpOnly cookies. It is designed for a trusted private pilot; it is not individual user accounts or tenant isolation. Requests are capped at 120 paid-capable API operations per minute and 20 new conversations per hour across the pilot. These are abuse controls, not a dollar spending cap. Keep provider account budgets in place.

Invitations expire after 24 hours. Replace or revoke one from **Invite participant**. Already-issued streaming credentials may remain usable with their provider until the stream or token expires; revocation immediately blocks new app access, and connected screens stop on their next poll. End a full live voice call before replacing its link. Ended rooms cannot request new transcription tokens. The setup page keeps shortcuts to the 12 most recent conversations on this browser, so you can reopen a call after closing its tab. The summary page can permanently delete the saved conversation and invitation from the application database. Deletion does not erase provider logs, exported files, SQLite free pages, or backups. There is no automatic transcript retention cleanup yet.

## Validation and remaining release work

```bash
./venv/bin/python -m unittest discover -s tests
npm run check
npm test
```

Automated checks cover independent host/participant clients, approval gates, interruption races, invitation lifecycle, private-pilot access, persistence, and frontend behavior. They do not replace browser, microphone, or real-user testing. CI runs the same checks on pushes and pull requests.

Before a general public launch: individual accounts and ownership checks, durable multi-worker coordination, per-account billing/quotas, a retention policy, provider production agreements, monitoring/backups, and measured accessibility and voice quality. Current language/pricing heuristics are not a guarantee for every real business commitment. Phone-number dialing is not integrated. This release is a private browser pilot.
