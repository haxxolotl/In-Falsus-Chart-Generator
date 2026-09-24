import hashlib,json,pathlib
OUT=STAGE=pathlib.Path(".")
def guid(name): return hashlib.md5(name.encode("utf-8")).hexdigest()
def sha(path): return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()
def save(value,path):
 path=pathlib.Path(path);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")
