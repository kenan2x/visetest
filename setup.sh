#!/bin/bash
set -e

echo "🔧 Kosmos Vize Checker kurulumu başlıyor..."

# 1. Repo'yu çek
echo "📦 Repo klonlanıyor..."
cd ~
if [ -d "visetest" ]; then
    cd visetest && git pull
else
    git clone https://github.com/kenan2x/visetest.git && cd visetest
fi

# 2. Playwright kur
echo "🌐 Playwright kuruluyor..."
pip3 install playwright
python3 -m playwright install chromium

# 3. Test mesajı
echo "📱 Test bildirimi gönderiliyor..."
HEADLESS=1 python3 ~/visetest/kosmos_checker.py --test

# 4. Uyku modunu kapat
echo "💤 Uyku modu kapatılıyor..."
sudo pmset -a sleep 0

# 5. LaunchAgent kur
echo "⚙️ Servis kuruluyor (10 dk aralık)..."
cat > ~/Library/LaunchAgents/com.kenan.kosmoschecker.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.kenan.kosmoschecker</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/Users/kenan/visetest/kosmos_checker.py</string>
        <string>--once</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>HEADLESS</key>
        <string>1</string>
        <key>NTFY_TOPIC</key>
        <string>vizexkk-test</string>
    </dict>
    <key>StartInterval</key>
    <integer>600</integer>
    <key>StandardOutPath</key>
    <string>/Users/kenan/visetest/checker.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/kenan/visetest/checker.log</string>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
EOF

# Varsa eskisini kaldır
launchctl unload ~/Library/LaunchAgents/com.kenan.kosmoschecker.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.kenan.kosmoschecker.plist

# 6. Doğrula
echo ""
echo "✅ Kurulum tamamlandı!"
launchctl list | grep kosmos
echo ""
echo "📋 Log takibi: tail -f ~/visetest/checker.log"
echo "🛑 Durdurmak için: launchctl unload ~/Library/LaunchAgents/com.kenan.kosmoschecker.plist"
