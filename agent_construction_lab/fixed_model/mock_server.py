"""Explicit scripted localhost HTTP fixture. Never use its scores as research evidence."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from .constructors import make_harness
from .client import ModelConfig

MOCK_MODEL="mock-fixed-model-v1"


def mock_manifest():
    names=("source.stock","source.supplies","dev.storage","target.warehouse","target.office")
    domains={name:dict(profile=dict(description="Inventory quantity changes using a documented stateful API",fixture_only=True),
        features=dict(requires_write=1.0,workflow_size=(i+1)/5)) for i,name in enumerate(names)}
    tasks=[]
    for i,name in enumerate(names):
        for j in range(3 if name.startswith("target") else 1):
            role="source" if name.startswith("source") else "dev" if name.startswith("dev") else "support" if j==0 else "query"
            tasks.append(dict(task_id=f"{name}.{j}",domain_id=name,split=role,
                payload=dict(item_id=f"sku-{i}-{j}",initial_quantity=2,target_quantity=6+i+j)))
    return dict(schema_version=1,benchmark="builtin",domains=domains,tasks=tasks)


def _response(body):
    messages=body["messages"]
    if not body.get("tools") and len(messages)==1:
        prompt=messages[0]["content"]
        content=json.dumps({"candidate_id":"c01"}) if prompt.startswith("Return exactly JSON with candidate_id") else json.dumps(make_harness(["plan","verify"]))
        message=dict(role="assistant",content=content)
    else:
        # Public task instruction is the only source of the requested target.
        user=next(m["content"] for m in messages if m["role"]=="user")
        decoder=json.JSONDecoder()
        payload=None
        for index,char in enumerate(user):
            if char=="{":
                try: candidate,_=decoder.raw_decode(user[index:])
                except ValueError: continue
                if isinstance(candidate,dict) and {"item_id","target_quantity"}<=candidate.keys():
                    payload=candidate;break
        if payload is None: raise ValueError("Mock fixture instruction missing public inventory payload")
        turns=sum(m["role"]=="assistant" for m in messages)
        system=messages[0]["content"]
        improves=any(f"[{name}]" in system or f"Module: {name}" in system for name in ("plan","verify"))
        if turns==0:
            name,args="code_execution",{"code":f"print(apis.inventory.get_item(item_id={payload['item_id']!r}))"}
        elif not improves or turns>=3:
            name,args="finish",{"answer":"Scripted fixture finished; not a real model."}
        elif turns==1:
            name,args="code_execution",{"code":f"print(apis.inventory.set_quantity(item_id={payload['item_id']!r}, quantity={payload['target_quantity']!r}))"}
        else:
            name,args="code_execution",{"code":"print(apis.supervisor.complete_task())"}
        if body.get("tools"):
            message=dict(role="assistant",content=None,tool_calls=[dict(id=f"mock-{turns}",type="function",function=dict(name=name,arguments=json.dumps(args)))])
        else:
            message=dict(role="assistant",content=json.dumps(dict(action="execute" if name=="code_execution" else "finish",**args)))
    input_tokens=max(1,len(json.dumps(messages))//4)
    output_tokens=max(1,len(json.dumps(message))//4)
    return dict(id="scripted-local-response",model=MOCK_MODEL,choices=[dict(message=message,finish_reason="tool_calls" if message.get("tool_calls") else "stop")],
        usage=dict(prompt_tokens=input_tokens,completion_tokens=output_tokens,total_tokens=input_tokens+output_tokens,
            prompt_tokens_details={"cached_tokens":0},completion_tokens_details={"reasoning_tokens":0}))


@contextmanager
def local_mock_model():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                if self.path!="/v1/chat/completions": raise ValueError("Unexpected path")
                body=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                encoded=json.dumps(_response(body)).encode()
                self.send_response(200)
            except Exception as error:
                encoded=json.dumps({"error":str(error)}).encode();self.send_response(400)
            self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(encoded)))
            self.end_headers();self.wfile.write(encoded)

        def log_message(self,*args): pass

    server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
    server.daemon_threads=True
    thread=threading.Thread(target=lambda:server.serve_forever(poll_interval=.01),daemon=True)
    thread.start()
    try:
        yield ModelConfig(model=MOCK_MODEL,model_snapshot=MOCK_MODEL,expected_response_model=MOCK_MODEL,
            base_url=f"http://127.0.0.1:{server.server_port}/v1",api_key_env=None,retries=0)
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)
