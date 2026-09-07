export function filterSeries(series, query) {
  const needle = String(query ?? '').normalize('NFKC').trim().toUpperCase();
  return series.filter(item => String(item.code).toUpperCase().includes(needle));
}

export function buildSeriesPreviews(videos) {
  const previews = new Map();
  for (const video of videos) {
    if (!/^https?:\/\//u.test(video.cover ?? '')) continue;
    const previous = previews.get(video.series);
    if (!previous || String(video.releaseDate) > String(previous.releaseDate)) {
      previews.set(video.series, video);
    }
  }
  return previews;
}
