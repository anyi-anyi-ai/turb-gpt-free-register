import subprocess
import time
import cloakbrowser

print("Starting cloakbrowser headless=False...")
b = cloakbrowser.launch(headless=False)
p = b.new_page()
p.goto("https://www.google.com")
print("Navigated. Checking running processes...")

ps = subprocess.check_output('powershell -Command "Get-Process | Where-Object { $_.ProcessName -like \'*chrome*\' -or $_.ProcessName -like \'*cloak*\' } | Select-Object Id, ProcessName, MainWindowHandle, MainWindowTitle | Format-Table -AutoSize"', shell=True).decode('utf-8', errors='ignore')
print(ps)

time.sleep(3)
b.close()
