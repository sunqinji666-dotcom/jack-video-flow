#!/usr/bin/env python3
"""Relay controller to browse chinadigitaltimes.net"""
import json
import sys
import time
import base64
import os
import websocket

WS_URL = "ws://119.29.193.16:25818"
TOKEN = os.environ.get("CLAUDE_RELAY_TOKEN", "")
WS_URL = os.environ.get("CLAUDE_RELAY_WS_URL", "ws://127.0.0.1:25818")
OUTPUT_DIR = os.environ.get("RELAY_OUTPUT_DIR", "./output")

cmd_id = [0]
def next_id():
    cmd_id[0] += 1
    return str(cmd_id[0])

def send(ws, action, **params):
    i = next_id()
    msg = {"type": "command", "id": i, "action": action, **params}
    ws.send(json.dumps(msg))
    return i

def recv(ws, timeout=30):
    ws.settimeout(timeout)
    while True:
        try:
            raw = ws.recv()
            data = json.loads(raw)
            return data
        except websocket.TimeoutError:
            return None

def recv_until(ws, expected_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        remaining = deadline - time.time()
        data = recv(ws, min(30, max(5, remaining)))
        if data is None:
            continue
        if data.get("type") == "result" and data.get("id") == expected_id:
            return data
        elif data.get("type") == "browser_status":
            print(f"[browser_status] connected={data.get('connected')}", file=sys.stderr)
        elif data.get("type") == "error":
            print(f"[error] {data.get('message')}", file=sys.stderr)
    return None

def main():
    ws = websocket.create_connection(WS_URL, timeout=10)

    # Auth
    ws.send(json.dumps({"type": "auth", "role": "controller", "token": TOKEN}))
    auth_resp = recv(ws, 10)
    print(f"[auth] {json.dumps(auth_resp)}", file=sys.stderr)

    # Wait for browser status
    for _ in range(15):
        data = recv(ws, 5)
        if data and data.get("type") == "browser_status":
            if data.get("connected"):
                print("[browser] connected!", file=sys.stderr)
                break
            else:
                print("[browser] not connected yet", file=sys.stderr)
        elif data:
            print(f"[msg] {json.dumps(data)}", file=sys.stderr)

    # Navigate to homepage
    print("[nav] Going to chinadigitaltimes.net/chinese/", file=sys.stderr)
    nav_id = send(ws, "navigate", url="https://chinadigitaltimes.net/chinese/")
    result = recv_until(ws, nav_id, timeout=60)
    if result and result.get("ok"):
        print(f"[nav] OK: tab={result.get('data',{}).get('tabId')}", file=sys.stderr)
    else:
        print(f"[nav] FAIL: {json.dumps(result)}", file=sys.stderr)
        ws.close()
        sys.exit(1)

    # Wait for page to render
    print("[wait] Waiting for page to render...", file=sys.stderr)
    time.sleep(8)

    # Extract article links via JavaScript
    print("[js] Extracting article links...", file=sys.stderr)
    js_id = send(ws, "evaluate", code="""
        Array.from(document.querySelectorAll('a[href*="/chinese/"]'))
            .filter(a => a.href && !a.href.includes('#') && a.textContent.trim().length > 10)
            .slice(0, 30)
            .map(a => ({title: a.textContent.trim(), url: a.href}))
    """)
    js_result = recv_until(ws, js_id, timeout=30)
    links = []
    if js_result and js_result.get("ok"):
        links = js_result.get("data", {}).get("result", [])
        print(f"[js] Found {len(links)} article links:", file=sys.stderr)
        for link in links[:15]:
            print(f"  • {link.get('title','')[:80]}", file=sys.stderr)
            print(f"    {link.get('url','')}", file=sys.stderr)
    else:
        print(f"[js] FAIL: {json.dumps(js_result)}", file=sys.stderr)

    # Get full page text
    print("[text] Getting full page text...", file=sys.stderr)
    text_id = send(ws, "get_text")
    text_result = recv_until(ws, text_id, timeout=30)
    if text_result and text_result.get("ok"):
        text = str(text_result.get("data", ""))
        path = f"{OUTPUT_DIR}/chinadigitaltimes-homepage.txt"
        with open(path, "w") as f:
            f.write(text)
        print(f"[text] Saved {len(text)} chars to {path}", file=sys.stderr)
    else:
        print(f"[text] FAIL: {json.dumps(text_result)}", file=sys.stderr)

    # If we found article links, pick the first one and navigate to it
    if links and len(links) > 0:
        # Pick the first substantive article (skip "首页" etc.)
        target = None
        for link in links:
            title = link.get('title', '')
            url = link.get('url', '')
            if len(title) > 15 and '/chinese/' in url:
                target = link
                break
        if not target:
            target = links[0]

        print(f"\n[nav] Going to article: {target.get('title','')[:60]}", file=sys.stderr)
        print(f"       {target.get('url','')}", file=sys.stderr)
        nav2_id = send(ws, "navigate", url=target.get('url',''))
        result2 = recv_until(ws, nav2_id, timeout=60)
        if result2 and result2.get("ok"):
            print("[nav] Article loaded!", file=sys.stderr)
        else:
            print(f"[nav] Article load FAIL: {json.dumps(result2)}", file=sys.stderr)
            ws.close()
            sys.exit(1)

        # Wait for article to render
        time.sleep(5)

        # Get article text
        article_text_id = send(ws, "get_text")
        article_result = recv_until(ws, article_text_id, timeout=30)
        if article_result and article_result.get("ok"):
            article_text = str(article_result.get("data", ""))
            safe_title = target.get('title','article').replace('/','_').replace(' ','_')[:50]
            path2 = f"{OUTPUT_DIR}/{safe_title}.txt"
            with open(path2, "w") as f:
                f.write(article_text)
            print(f"[article] Saved {len(article_text)} chars to {path2}", file=sys.stderr)
            # Print first 500 chars
            print(f"\n[article preview] (first 500 chars):", file=sys.stderr)
            print(article_text[:500], file=sys.stderr)
        else:
            print(f"[article] FAIL: {json.dumps(article_result)}", file=sys.stderr)

    ws.close()
    print("\n[Done]", file=sys.stderr)

if __name__ == "__main__":
    main()
