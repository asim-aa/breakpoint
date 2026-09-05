#!/bin/bash
# Replays real, previously-captured Breakpoint output for the README demo GIF.
# The content in sample_run.txt and sample_history.txt is not synthetic —
# it's the exact output from actual runs during this project's development
# (see README.md's "Real bugs found and fixed" section for the full story).
cd "$(dirname "$0")/.."

echo '$ python manual_run.py "merge overlapping intervals in a list of [start, end] pairs"'
sleep 0.4
cat demo/sample_run.txt
sleep 2.5

echo
echo '$ python cli.py history'
sleep 0.4
cat demo/sample_history.txt
sleep 2.5
