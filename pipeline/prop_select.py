"""Pick close-up prop colour textures (field objects, <=512, furniture/containers/stalls/food...) by name tokens.
usage: python -I prop_select.py <inventory.tsv> <out.json>
inventory.tsv: one row per colour texture (sha, name, w, h, layer = which pack layers already replace it).
The list was then reviewed by hand (wood/food flags); the final one is props_selection.json."""
import sys, csv, re, json, collections
def words(name):
    base = name.rsplit('.', 1)[0]
    return [w.lower() for w in re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?![a-z])', base)]
KW = set('''shop shops stall stalls atlas table tables desk chair chairs bench stool box boxes crate crates barrel barrels sack sacks
basket baskets cart wagon pot pots jar jars bottle bottles cup cups plate plates dish dishes bowl bowls shelf shelves counter bed beds
mattress lamp lamps candle candles lantern food meat meatshop fish fishes fishshop fruit fruits fruitshop bread vegetable vegetables vege cheese wine
bucket tool tools weapon weapons armor tent awning sign signboard chest trunk cabinet drawer wardrobe stand rack scale anvil kettle
stove oven firewood ladder carpet rug curtain vase cushion pillow blanket book books bookcase scroll globe clock mirror pan cauldron
tub pail woodenbox props prop accessory kitchen washing barrelstand spice goods market'''.split())
EX = set('''desktop flower flowers leaf leaves tree trees plant plants grass sky cloud clouds billboard moss bush fern ivy vine weed
azalea spirea berry far lod grunge sbs root crystal'''.split())
rows = list(csv.DictReader(open(sys.argv[1]), delimiter='\t')); sel = []; why = collections.Counter()
for r in rows:
    if max(int(r['w']), int(r['h'])) > 512: continue
    if any(x in r['layer'] for x in ('best', 'cavity', 'special')): continue
    ws = set(words(r['name'])); hit = ws & KW
    if not hit or ws & EX: continue
    sel.append(r['sha']); why.update(hit)
json.dump(sel, open(sys.argv[2], 'w')); print('selected', len(sel)); print(why.most_common(50))
