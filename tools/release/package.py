"""Zip the installer for a release: Install.bat at the top, installer/ scripts, data/manifest.txt.gz.

usage: python -I package.py <repo dir> <release dir with manifest.txt.gz> <version>
"""
import sys, os, json, zipfile

TXT = """Metaphor: ReFantazio Remaster {v}
==================================

1. Install Reloaded-II and Metaphor Essentials (https://gamebanana.com/mods/548481),
   and turn on "Force 4K Assets" in Metaphor Essentials (Reloaded-II > right click > Configure Mod).
2. Double-click Install.bat. It finds the game and Reloaded-II, downloads the data parts
   ({dl_all:.1f} GB) from GitHub and builds the mod ({out:.0f} GB) from your own base.cpk.
   Already downloaded the .bin parts by hand? Put them in the "data" folder first.
   Updating from an earlier version? Run this Install.bat the same way: files that did not change
   are kept, and the textures already installed are reused, so it only downloads the new data ({dl_new:.1f} GB).
3. In Reloaded-II, tick "Metaphor: ReFantazio Remaster" and start the game from there.

Full instructions, credits and licence: https://github.com/oxenar7/metaphor-refantazio-remaster
"""


def main():
    repo, rel, v = sys.argv[1:4]
    top = f'MetaphorRemaster-{v}'
    out = os.path.join(rel, f'MetaphorRemaster-{v}-installer.zip')
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        def add_text(arc, text, crlf=True):
            if crlf: text = text.replace('\r\n', '\n').replace('\n', '\r\n')
            z.writestr(f'{top}/{arc}', text.encode('utf-8'))
        add_text('Install.bat', open(os.path.join(repo, 'installer', 'Install.bat'), encoding='utf-8').read())
        summ = json.load(open(os.path.join(rel, 'release_summary.json')))
        add_text('README.txt', TXT.format(v=v, out=summ['out_bytes'] / 1e9, dl_all=summ['packed_bytes'] / 1e9,
                                          dl_new=summ.get('new_bytes', summ['packed_bytes']) / 1e9))
        for f in ('Install.ps1', 'Builder.cs', 'ModConfig.json'):
            add_text(f'installer/{f}', open(os.path.join(repo, 'installer', f), encoding='utf-8').read())
        add_text('installer/LICENSE.txt', open(os.path.join(repo, 'LICENSE'), encoding='utf-8').read())
        z.write(os.path.join(rel, 'manifest.txt.gz'), f'{top}/data/manifest.txt.gz')
    print(out, os.path.getsize(out))
    summ = json.load(open(os.path.join(rel, 'release_summary.json')))
    own = [p for p in summ['parts'] if not p.get('reused')]   # parts kept from an earlier release are listed there
    with open(os.path.join(rel, 'SHA256SUMS.txt'), 'w', newline='\n') as s:
        for p in own: s.write(f"{p['sha256']}  {p['name']}\n")
    print('SHA256SUMS.txt:', len(own), 'parts')


if __name__ == '__main__':
    main()
