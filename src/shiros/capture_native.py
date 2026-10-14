"""Opt-in CDP collector for a single conversation on an already-open local interface."""

import json
import urllib.request
from urllib.parse import urlsplit

import websocket

from shiros.capture import CaptureSnapshot, key

EXPRESSION = r"""(() => {
 const area=document.querySelector('main,[data-slot="conversation.session"]');
 if(!area)return {url:location.href,messages:[]};
 const selector='[data-message-author-role],[data-role="user"],'+
   '[data-role="assistant"],[data-chat-anchor-key]';
 const rows=[...area.querySelectorAll(selector)];
 const hash=value=>[2166136261,3335557771].map(seed=>Array.from(value).reduce(
   (h,c)=>Math.imul(h^c.charCodeAt(0),16777619)>>>0,seed).toString(16)).join('-');
 const messages=rows.map((node,n)=>{
   const kind=node.dataset.messageAuthorRole||node.dataset.role||node.dataset.chatFlowKind||'';
   const role=kind.includes('user')?'user':kind.includes('assistant')?'assistant':null;
   const id=node.dataset.messageId||node.dataset.chatAnchorKey||'snapshot-'+hash(node.innerText);
   const attachments=[...node.querySelectorAll('a[download]')].map((a,i)=>({
     id:'native-file-'+id+'-'+i,filename:a.download||a.textContent.trim()||'attachment',
     reference:a.href.split('?')[0]}));
   return {id,role,text:node.innerText||'',attachments};
 }).filter(x=>x.role&&x.text.trim());
 return {url:location.href,messages};
})()"""


def collect_native(grant: dict) -> CaptureSnapshot:
    base = grant["native_endpoint"].rstrip("/")
    endpoint = urlsplit(base)
    if endpoint.scheme != "http" or endpoint.hostname != "127.0.0.1" or not endpoint.port:
        raise ValueError("capture.invalid_native_endpoint")
    # Ignore proxy settings, credentials, and unrelated windows.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(base + "/json/list", timeout=2) as response:
            targets = json.load(response)
    except Exception:
        raise ValueError("capture.native_interface_unavailable") from None
    selected = []
    for target in targets:
        url = urlsplit(target.get("url", ""))
        if target.get("type") != "page":
            continue
        try:
            expected = key(target.get("url", ""))
        except ValueError:
            continue
        if grant["provider"] == "gpt" and url.hostname != "chatgpt.com":
            continue
        if grant["provider"] == "deepseek" and url.hostname != "chat.deepseek.com":
            continue
        if grant["provider"] not in ("gpt", "deepseek") and url.hostname not in (
            "127.0.0.1",
            "localhost",
        ):
            continue
        if expected == grant["conversation_id"]:
            selected.append(target)
    if len(selected) != 1:
        raise ValueError("capture.native_conversation_not_found")
    wsurl = selected[0].get("webSocketDebuggerUrl", "")
    socket_url = urlsplit(wsurl)
    if (
        socket_url.scheme != "ws"
        or socket_url.hostname != "127.0.0.1"
        or socket_url.port != endpoint.port
    ):
        raise ValueError("capture.invalid_native_endpoint")
    client = websocket.create_connection(
        wsurl, timeout=3, suppress_origin=True, http_proxy_host=None
    )
    try:
        client.send(
            json.dumps(
                {
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {"expression": EXPRESSION, "returnByValue": True},
                }
            )
        )
        while True:
            response = json.loads(client.recv())
            if response.get("id") != 1:
                continue
            if response.get("error") or response.get("result", {}).get("exceptionDetails"):
                raise ValueError("capture.native_read_failed")
            data = response.get("result", {}).get("result", {}).get("value") or {}
            actual = urlsplit(data.get("url", ""))
            if key(data.get("url", "")) != grant["conversation_id"]:
                raise PermissionError("capture.conversation_mismatch")
            if actual.hostname != urlsplit(selected[0].get("url", "")).hostname:
                raise PermissionError("capture.conversation_mismatch")
            messages = data.get("messages") or []
            if not messages:
                raise ValueError("capture.no_messages")
            return CaptureSnapshot.model_validate(
                {
                    "provider": grant["provider"],
                    "conversation_id": grant["conversation_id"],
                    "messages": messages,
                    "message_order": [message["id"] for message in messages],
                }
            )
    finally:
        client.close()
