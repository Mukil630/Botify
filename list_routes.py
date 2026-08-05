from app import app
rules = [str(r) for r in app.url_map.iter_rules()]
for r in sorted(rules):
    print(r)
