# Railway Deployment Guide

## Prerequisites

- Railway account (free tier)
- GitHub account (optional, for auto-deploy)

## Step 1: Prepare Environment Variables

You'll need to set these in Railway dashboard:

- `ELEVENLABS_API_KEY`
- `ELEVENLABS_AGENT_ID`
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_PHONE_NUMBER`

## Step 2: Deploy to Railway

### Option A: Deploy from GitHub (Recommended)

1. Push this folder to GitHub repository
2. Go to [railway.app](https://railway.app)
3. Click "New Project" → "Deploy from GitHub repo"
4. Select your repository
5. Railway will auto-detect Python and deploy

### Option B: Deploy using Railway CLI

```bash
# Install Railway CLI
npm i -g @railway/cli

# Login
railway login

# Initialize project
railway init

# Deploy
railway up
```

## Step 3: Configure Environment Variables

1. Go to your Railway project dashboard
2. Click "Variables" tab
3. Add all environment variables listed above
4. Click "Deploy" to restart with new variables

## Step 4: Get Your Railway URL

After deployment:

1. Go to "Settings" tab
2. Click "Generate Domain"
3. Copy the URL (e.g., `https://your-app.up.railway.app`)

## Step 5: Update NodeMCU Code

Replace the `serverUrl` in NodeMCU code:

```cpp
String serverUrl = "https://your-app.up.railway.app/trigger-gas-alert";
```

## Step 6: Test

1. Upload updated NodeMCU code
2. Trigger gas sensor
3. Check Railway logs to confirm API call received

## Troubleshooting

- Check Railway logs for errors
- Verify all environment variables are set
- Ensure Twilio credentials are correct
- Test endpoint: `https://your-app.up.railway.app/docs`
