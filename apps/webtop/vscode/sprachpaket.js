// Erzeugt languagepacks.json für VS Code (deutsches Sprachpaket), läuft beim Image-Build.
// VS Code legt die Datei sonst erst beim ersten Start an und ist deshalb nur beim ZWEITEN
// Start deutsch. 50-oaap-init kopiert sie nach ~/.config/Code/languagepacks.json.
// Hash wie in VS Code (platform/languagePacks): md5 über uuid + Version jeder Erweiterung.
// Aufruf: node sprachpaket.js <extensions-dir im Image> <extensions-dir im Home> <ziel>
const fs = require('fs'), path = require('path'), crypto = require('crypto');
const [quelle, home, ziel] = process.argv.slice(2);

const registry = JSON.parse(fs.readFileSync(path.join(quelle, 'extensions.json'), 'utf8'));
const ergebnis = {};
for (const e of registry) {
  const dir = e.relativeLocation || path.basename(e.location.path);
  const pkg = JSON.parse(fs.readFileSync(path.join(quelle, dir, 'package.json'), 'utf8'));
  for (const l of pkg.contributes?.localizations || []) {
    const lp = ergebnis[l.languageId] ||= { hash: '', extensions: [], translations: {}, label: l.localizedLanguageName };
    lp.extensions.push({ extensionIdentifier: { id: e.identifier.id, uuid: e.identifier.uuid }, version: e.version });
    for (const t of l.translations) lp.translations[t.id] = path.join(home, dir, t.path);
  }
}
for (const lp of Object.values(ergebnis)) {
  const md5 = crypto.createHash('md5');
  for (const x of lp.extensions) md5.update(x.extensionIdentifier.uuid || x.extensionIdentifier.id).update(x.version);
  lp.hash = md5.digest('hex');
}
if (!ergebnis.de) { console.error('kein deutsches Sprachpaket gefunden'); process.exit(1); }
fs.writeFileSync(ziel, JSON.stringify(ergebnis));
console.log(`languagepacks.json: ${Object.keys(ergebnis).join(', ')}, ${Object.keys(ergebnis.de.translations).length} Übersetzungen`);
