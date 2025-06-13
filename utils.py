import os

USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/115.0.0.0 Safari/537.36'
)

def find_chrome_executable():
    """Return path to Chrome executable or None if not found."""
    possible_paths = [
        r'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
        r'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
        os.path.expandvars(r'%LOCALAPPDATA%\\Google\\Chrome\\Application\\chrome.exe'),
        '/usr/bin/google-chrome',
        '/usr/local/bin/google-chrome',
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    ]
    for path in possible_paths:
        if os.path.exists(path):
            return path
    return None
