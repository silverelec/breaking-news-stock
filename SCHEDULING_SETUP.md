# Scheduling Setup Guide

Choose one of the two options below. **GitHub Actions is recommended** — your machine doesn't need to be on.

---

## Option A: GitHub Actions (Recommended — Cloud, Always On)

### Prerequisites
- GitHub account
- Code pushed to a GitHub repository (can be private)

### Step 1: Push code to GitHub
```bash
git init
git add .
git commit -m "Initial market brief automation"
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```

### Step 2: Add secrets to GitHub
Go to your repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

Add these secrets (copy values from your `.env` file):
| Secret Name | Value |
|-------------|-------|
| `NEWSAPI_KEY` | your NewsAPI key |
| `FINNHUB_API_KEY` | your Finnhub key |
| `POLYGON_API_KEY` | your Polygon.io key |
| `ALPHAVANTAGE_KEY` | your Alpha Vantage key |
| `ANTHROPIC_API_KEY` | your Anthropic key |
| `EMAIL_FROM` | your Gmail address |
| `EMAIL_TO` | recipient Gmail address |
| `EMAIL_PASSWORD` | your Gmail App Password |
| `RECIPIENT_NAME` | Rahul |

### Step 3: Create the workflow file
The file `.github/workflows/daily_brief.yml` is already created. GitHub will automatically detect and run it.

### Step 4: Verify it runs
- Go to your repo → **Actions** tab
- You should see "Daily Market Brief" workflow
- Click **Run workflow** to test manually
- Check the logs to confirm success

### Schedule Details
- Runs at **02:30 UTC = 8:00 AM IST** daily
- Email arrives ~8:05–8:10 AM IST (before market opens at 9:15 AM IST)
- To change time, edit the `cron` line in `.github/workflows/daily_brief.yml`

---

## Option B: Windows Task Scheduler (Local — Machine Must Be On)

### Step 1: Find your Python path
Open Command Prompt and run:
```cmd
where python
```
Note the full path (e.g., `C:\Users\Rahul\AppData\Local\Programs\Python\Python311\python.exe`)

### Step 2: Create a batch file
Create `run_brief.bat` in your project folder with these contents:
```batch
@echo off
cd /d "C:\Users\Rahul\Documents\Agentic Workflows\Breaking News Stock"
"C:\Users\Rahul\AppData\Local\Programs\Python\Python311\python.exe" tools/run_daily_brief.py >> .tmp/task_scheduler.log 2>&1
```
Adjust paths to match your system.

### Step 3: Open Task Scheduler
- Press `Win + R`, type `taskschd.msc`, press Enter
- Click **Create Basic Task** in the right panel

### Step 4: Configure the task
1. **Name**: `Daily Market Brief`
2. **Description**: `Send morning Indian stock market brief email`
3. **Trigger**: Daily
4. **Start time**: `8:00:00 AM` (local time — must be IST if your PC is in IST)
5. **Action**: Start a program
6. **Program**: Browse to your `run_brief.bat` file
7. **Start in**: `C:\Users\Rahul\Documents\Agentic Workflows\Breaking News Stock`

### Step 5: Configure additional settings
After creating the task, right-click it → **Properties**:
- **General tab**: Check "Run whether user is logged on or not"
- **Conditions tab**: Uncheck "Start the task only if the computer is on AC power" (optional)
- **Settings tab**: Check "Run task as soon as possible after a scheduled start is missed"

### Step 6: Test the task
Right-click the task → **Run** to test it manually. Check `.tmp/task_scheduler.log` for output.

---

## Verifying the Schedule Works

After your first scheduled run, check:

1. **Email in inbox** — Look for subject `📈 Your Market Brief — Mon DD Mon`
2. **Run log** — Check `.tmp/run_log.json`:
   ```bash
   python -c "import json; r = json.load(open('.tmp/run_log.json')); print(r[-1]['status'], r[-1]['timestamp'])"
   ```
3. **GitHub Actions** (if using Option A) — Check the Actions tab in your repo

---

## Changing the Send Time

Indian market opens at **9:15 AM IST**. The brief is scheduled for **8:00 AM IST** to give you time to read it.

To change the time:
- **GitHub Actions**: Edit the `cron:` line in `.github/workflows/daily_brief.yml`
  - Format: `MM HH * * *` in UTC (IST = UTC + 5:30)
  - 8:00 AM IST = `30 2 * * *` (02:30 UTC)
  - 7:00 AM IST = `30 1 * * *` (01:30 UTC)
- **Task Scheduler**: Change the trigger time in the task properties
