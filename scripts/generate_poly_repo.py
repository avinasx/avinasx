import os
import sys
import requests
import subprocess
from datetime import datetime, timezone

# -----------------------------------------------------------------------------
# CONFIGURATION
# -----------------------------------------------------------------------------
GITHUB_USERNAME = "avinasx" 
OUTPUT_REPO_DIR = "combined_activity_repo"
MAX_REPOS = 5 
EVENTS_URL = f"https://api.github.com/users/{GITHUB_USERNAME}/events/public"

def run_cmd(args, cwd=None, env=None):
    # merge env with system env
    final_env = os.environ.copy()
    if env:
        final_env.update(env)
    subprocess.check_call(args, cwd=cwd, env=final_env)

def main():
    print(f"Fetching events for {GITHUB_USERNAME}...")
    try:
        resp = requests.get(EVENTS_URL)
        resp.raise_for_status()
        events = resp.json()
    except Exception as e:
        print(f"Error fetching GitHub events: {e}")
        sys.exit(1)

    # Filter for PushEvents
    push_events = [e for e in events if e["type"] == "PushEvent"]
    
    # Sort by date (oldest first)
    push_events.sort(key=lambda x: x["created_at"])

    if not push_events:
        print("No recent push events found.")
        # Create dummy repo so workflow doesn't fail on CD, 
        # but warn that it will be empty.
        # Actually better to fail so user knows why no video.
        sys.exit(1)

    print(f"Found {len(push_events)} push events. Generating synthetic history...")

    # 1. Initialize the synthetic repository
    if os.path.exists(OUTPUT_REPO_DIR):
        import shutil
        shutil.rmtree(OUTPUT_REPO_DIR)
    os.makedirs(OUTPUT_REPO_DIR)
    
    run_cmd(["git", "init"], cwd=OUTPUT_REPO_DIR)
    
    # Configure user for this repo to avoid "Please tell me who you are"
    run_cmd(["git", "config", "user.email", "bot@example.com"], cwd=OUTPUT_REPO_DIR)
    run_cmd(["git", "config", "user.name", "ActivityBot"], cwd=OUTPUT_REPO_DIR)

    # Root commit
    first_date = push_events[0]["created_at"]
    run_cmd([
        "git", "commit", "--allow-empty", "-m", "Init History", 
        "--date", first_date
    ], cwd=OUTPUT_REPO_DIR, env={"GIT_AUTHOR_DATE": first_date, "GIT_COMMITTER_DATE": first_date})
    
    # Rename current branch to main to ensure consistency
    run_cmd(["git", "branch", "-M", "main"], cwd=OUTPUT_REPO_DIR)

    repos_seen = set()
    created_branches = set()
    
    # We will track active branches to merge them at the end
    active_branches = set()

    for event in push_events:
        repo_name = event["repo"]["name"]
        short_name = repo_name.split("/")[-1]
        
        # Filter Logic
        if short_name not in repos_seen:
             if len(repos_seen) >= MAX_REPOS:
                 continue 
             repos_seen.add(short_name)

        # Branch Management
        if short_name not in created_branches:
            # Create new branch from main
            run_cmd(["git", "checkout", "main"], cwd=OUTPUT_REPO_DIR)
            run_cmd(["git", "checkout", "-b", short_name], cwd=OUTPUT_REPO_DIR)
            created_branches.add(short_name)
            active_branches.add(short_name)
        else:
            # Switch to existing branch
            run_cmd(["git", "checkout", short_name], cwd=OUTPUT_REPO_DIR)

        # Replay Commits
        timestamp = event["created_at"]
        env = {"GIT_AUTHOR_DATE": timestamp, "GIT_COMMITTER_DATE": timestamp}
        
        commits = event["payload"].get("commits", [])
        if not commits:
             # If no commits in payload, force one to mark activity
             commits = [{"message": "Update"}]
             
        for commit in commits:
            msg = commit["message"].split("\n")[0]
            # Sanitize message
            run_cmd(
                ["git", "commit", "--allow-empty", "-m", f"{short_name}: {msg}"],
                cwd=OUTPUT_REPO_DIR,
                env=env
            )

    # Final Merge: Octopus merge all branches into main so HEAD sees everything
    print("Merging all branches into main...")
    run_cmd(["git", "checkout", "main"], cwd=OUTPUT_REPO_DIR)
    
    # Git merge expects branch names as arguments
    if active_branches:
        # Sort for consistency
        branches_to_merge = sorted(list(active_branches))
        # We use --no-edit to avoid opening editor, and allow-unrelated-histories just in case (though they share root)
        cmd = ["git", "merge", "--no-edit"] + branches_to_merge
        # We timestamp this merge as 'now' or last event? Let's use 'now' (default)
        try:
            run_cmd(cmd, cwd=OUTPUT_REPO_DIR)
        except Exception as e:
            print(f"Merge failed (possibly empty branches?): {e}")

    print("Synthetic repository generation complete.")

if __name__ == "__main__":
    main()
