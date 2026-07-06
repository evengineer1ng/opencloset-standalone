# HANDOFF: Upload OpenCloset to GitHub

## Current State (as of this handoff)

### What's Done
- Git is installed and initialized in `D:\openclaw\opencloset` (bare repo, no commits yet)
- GitHub repo exists at: `https://github.com/evengineer1ng/OpenCloset`
- GitHub CLI (`gh`) was installed via `choco install gh` (user ran it in admin PowerShell)
- `gh` is installed but **NOT in PATH** â it's at `C:\ProgramData\chocolatey\bin\gh.exe`
- No git remote is configured yet
- All files are untracked

### What Needs to Happen
Upload the entire `D:\openclaw\opencloset` folder to the GitHub repo.

## Step-by-Step Instructions for Next Agent

### 1. Add gh to PATH
```powershell
$env:PATH = "$env:PATH;C:\ProgramData\chocolatey\bin"
```
Verify:
```powershell
& "C:\ProgramData\chocolatey\bin\gh.exe" --version
```

### 2. Authenticate with GitHub
```powershell
& "C:\ProgramData\chocolatey\bin\gh.exe" auth login
```
- Choose "GitHub.com"
- Choose "Login with a web browser" (easiest â opens browser for OAuth)
- Or use a Personal Access Token if browser auth fails in this environment

### 3. Configure Git User (if not already set)
```powershell
git config --global user.name "evengineer1ng"
git config --global user.email "YOUR_EMAIL@EXAMPLE.COM"
```
> NOTE: Use the email associated with the GitHub account.

### 4. Add Remote and Push
```powershell
cd D:\openclaw\opencloset

# Add the remote
git remote add origin https://github.com/evengineer1ng/OpenCloset.git

# Create a .gitignore to exclude large/binary files and temp dirs
# Suggested .gitignore content:
# /tmp/
# /memory/
# *.log
# *.tmp
# /__pycache__/
# *.pyc
# .clawhub/
# .openclaw/

# Add files
git add .

# Initial commit
git commit -m "Initial commit: OpenCloset project"

# Push to GitHub
git branch -M main
git push -u origin main
```

### 5. Verify
```powershell
& "C:\ProgramData\chocolatey\bin\gh.exe repo view evengineer1ng/OpenCloset
```
Or just check `https://github.com/evengineer1ng/OpenCloset` in a browser.

## Important Notes
- The repo is empty (no commits yet on GitHub side)
- The local git repo has no commits either â this is a fresh start
- `gh` binary location: `C:\ProgramData\chocolatey\bin\gh.exe`
- Working directory: `D:\openclaw\opencloset`
- Branch name: `main` (or `master` â either works, just be consistent)
- Consider creating a `.gitignore` before committing to avoid uploading temp files, memory logs, etc.

## Files in the Repo (untracked, ready to add)
```
AGENTS.md, HEARTBEAT.md, IDENTITY.md, MEMORY.md, SOUL.md, TOOLS.md, USER.md, VISION.md
artifacts/, buddy/, context_guard.md, handoff_current.md, leaderboard.md
localmodelwork.md, memory/, models/, phoneclaw/, self-improving/, skills/
system_registry.json, templates/, tmp/, tools/, .clawhub/, .openclaw/
```
