Create and run Python scripts on a schedule. Scripts fetch data, the LLM crafts the email content.

Tools:
- create_script(name, code, schedule) — create a new script
- run_script(name) — run a script manually now
- list_scripts() — list saved scripts
- delete_script(name) — remove a script

Schedule format:
- daily 08:00 — every day at 08:00 UTC
- weekdays 09:30 — Mon-Fri at 09:30 UTC
- hourly :15 — at minute 15 of every hour
- interval 30 — every 30 minutes
