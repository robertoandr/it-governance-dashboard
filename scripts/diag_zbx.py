import os, requests, urllib3, json
urllib3.disable_warnings()

env_vars = {}
with open("/opt/it-gov-dashboard/.env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            env_vars[k] = v
            os.environ[k] = v

print("Variaveis encontradas no .env:")
for k, v in env_vars.items():
    if any(s in k.upper() for s in ["TOKEN", "PASS", "SECRET", "KEY"]):
        masked = v[:6] + "***"
    else:
        masked = v[:30]
    print("   " + k + " = " + masked)

token = None
for cand in ["ZABBIX_TOKEN", "ZABBIX_API_TOKEN", "ZBX_TOKEN",
             "ZABBIX_AUTH_TOKEN", "API_TOKEN", "TOKEN"]:
    if env_vars.get(cand):
        token = env_vars[cand]
        print("\nToken encontrado em: " + cand)
        break

url = env_vars.get("ZABBIX_URL")

if not token:
    print("\nERRO: nenhum token encontrado no .env")
    raise SystemExit(1)

print("\nTestando " + str(url) + " ...")
r = requests.post(url, json={
    "jsonrpc": "2.0", "method": "apiinfo.version",
    "params": {}, "id": 1
}, verify=False, timeout=10).json()
print("   Versao Zabbix: " + str(r.get("result", "ERRO")))

print("\nTestando auth via Bearer header...")
r = requests.post(url,
    json={"jsonrpc": "2.0", "method": "host.get",
          "params": {"countOutput": True}, "id": 2},
    headers={"Content-Type": "application/json-rpc",
             "Authorization": "Bearer " + token},
    verify=False, timeout=10).json()
if "result" in r:
    print("   OK! Total de hosts: " + str(r["result"]))
else:
    print("   ERRO: " + json.dumps(r.get("error", {})))
