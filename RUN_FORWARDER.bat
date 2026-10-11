@echo off
cd /d C:\p2p-bot
echo Starting forwarder...
python forwarder.py > forwarder_error.txt 2>&1
echo Forwarder stopped. Check forwarder_error.txt






