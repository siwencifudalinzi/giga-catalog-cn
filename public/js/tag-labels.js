// Display-only corrections keyed by the official Japanese name. IDs and
// assignments remain unchanged; previous labels remain searchable.
const corrections = new Map([
  ['金髪', '金发'],
  ['ニューヒロイン', '新登场女英雄'],
]);
const originalNames = new Set([
  'サムライジャー', 'フォンテーヌ', 'ステキ仮面',
  'マーラティ', 'チェイスティー', 'グレートガール',
]);

export function tagPresentation(nameJa, oldName) {
  const nameZh = corrections.get(nameJa) ?? (originalNames.has(nameJa) ? nameJa : oldName);
  return {
    nameZh,
    aliases: Object.freeze([...new Set([oldName, nameJa, nameZh])]),
  };
}
