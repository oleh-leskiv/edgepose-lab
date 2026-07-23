import glob, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / ".streamlit" / "secrets.toml"
FIELDS = ["type","project_id","private_key_id","private_key","client_email",
          "client_id","auth_uri","token_uri","auth_provider_x509_cert_url",
          "client_x509_cert_url","universe_domain"]

arg = sys.argv[1] if len(sys.argv) > 1 else None
if arg:
    matches = glob.glob(str(Path(arg).expanduser()))
else:
    matches = glob.glob(str(Path.home() / "Downloads" / "*firebase-adminsdk*.json"))
if not matches:
    sys.exit("Не знайшов JSON-ключ. Вкажи шлях: uv run python scripts/make_secrets.py ~/Downloads/твій-файл.json")

key_path = Path(max(matches, key=lambda p: Path(p).stat().st_mtime))
info = json.loads(key_path.read_text(encoding="utf-8"))
esc = lambda v: v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

pw = input("Пароль для команди [EdgePose2026]: ").strip() or "EdgePose2026"
lines = ["# Generated automatically - do not commit.", "", f'app_password = "{esc(pw)}"', "", "[firebase]"]
lines += [f'{f} = "{esc(str(info[f]))}"' for f in FIELDS if f in info]
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")

print(f"\nГотово: {OUT}")
print(f"Service account: {info['client_email']}")
print(f"Пароль: {pw}")
