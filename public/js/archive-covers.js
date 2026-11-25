// These two archives serve the original covers over HTTP. Match the exact
// source so a future change to a catalog entry uses the new cover normally.
const bundled = new Map();
const archives = {
  "g-history.jp": [
    "jdsd03", "jdsd04", "jdsd08", "jhgd01", "jjod01", "jkat02", "jkat03",
    "nryo01", "nryo02", "nryo03", "nryo04", "nryo05", "nryo06", "nryo07", "nryo08",
    "nryo09", "nryo10", "nryo11", "nryo12", "nryo13", "nryo14", "nryo15", "nryo16",
    "nryo17", "nryo18", "nryo19", "nryo20", "nryo21", "nryo22", "nryo23", "nryo24",
    "pihp01", "pihp02", "piks01", "pisb01",
    "pitb01", "pitb02", "pitb03", "pitb04", "pitb05", "pitb06", "pitb07", "pitb08",
    "pitb09", "pitb10", "pitb11", "pitb12", "pitb13", "pitb14", "pitb15", "pitb16",
  ],
  "gallop.giga-web.jp": ["psd01", "psd02", "psd03", "psd04", "psd05", "psd06"],
};

for (const [host, files] of Object.entries(archives)) {
  for (const file of files) {
    const series = file.replace(/\d+$/u, "");
    const source = `http://${host}/db_titles/${series}/${file}/pac_s.jpg`;
    bundled.set(source, new URL(`../media/archive-covers/${file}.webp`, import.meta.url).href);
  }
}

export function displayCover(source) {
  return bundled.get(source) ?? source;
}
