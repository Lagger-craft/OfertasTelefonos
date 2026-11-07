const test = require('node:test');
const assert = require('node:assert/strict');
const {
  priceBand,
  latestSnapshot,
  currentOffers,
  buildPriceHistogram,
  buildPriceTrend,
  processorFor,
  compareModelPrices,
} = require('../dashboard-core.js');

const makeOffer = (model, price, extra = {}) => ({
  key: `${model}-${price}`,
  model,
  price,
  currency: 'CLP',
  available: true,
  ...extra,
});

const history = (snapshots) => ({
  schema_version: 1,
  budget: { currency: 'CLP', within_max: 340000, near_max: 370000 },
  last_collected_at: '2026-02-16T12:00:00+00:00',
  snapshots,
});

test('budget labels include exact cap and near-budget boundaries', () => {
  assert.equal(priceBand(340000), 'within_budget');
  assert.equal(priceBand(340001), 'near_budget');
  assert.equal(priceBand(370000), 'near_budget');
  assert.equal(priceBand(370001), 'excluded');
  assert.equal(priceBand(0), 'excluded');
});

test('latest snapshot is selected by ISO week and empty history is safe', () => {
  assert.equal(latestSnapshot(history({})), null);
  const latest = latestSnapshot(history({
    '2026-W05': { offers: [] },
    '2026-W07': { offers: [makeOffer('s23', 300000)] },
    '2026-W06': { offers: [] },
  }));
  assert.equal(latest.week, '2026-W07');
  assert.equal(latest.offers.length, 1);
});

test('current offers discard unavailable, unknown-model and above-watch-band records', () => {
  const snapshot = history({
    '2026-W07': { offers: [
      makeOffer('s23', 340000),
      makeOffer('s23_fe', 370000),
      makeOffer('s23', 350000, { available: false }),
      makeOffer('s23_ultra', 200000),
      makeOffer('s23_fe', 370001),
    ] },
  });
  assert.deepEqual(currentOffers(snapshot).map((offer) => offer.price), [340000, 370000]);
});

test('histogram counts the latest current offers by CLP price band', () => {
  const bins = buildPriceHistogram([
    makeOffer('s23', 240000),
    makeOffer('s23', 265000),
    makeOffer('s23_fe', 290000),
    makeOffer('s23_fe', 325000),
    makeOffer('s23_fe', 350000),
  ]);
  assert.deepEqual(bins.map((bin) => bin.count), [1, 1, 1, 1, 1]);
  assert.equal(bins.at(-1).label, '$340k–$370k');
});

test('trend shows the lowest observed offer per model each week and gaps as null', () => {
  const trend = buildPriceTrend(history({
    '2026-W06': { offers: [makeOffer('s23', 350000), makeOffer('s23_fe', 330000)] },
    '2026-W07': { offers: [makeOffer('s23', 320000)] },
  }));
  assert.deepEqual(trend.weeks, ['2026-W06', '2026-W07']);
  assert.deepEqual(trend.series.s23, [350000, 320000]);
  assert.deepEqual(trend.series.s23_fe, [330000, null]);
});

test('processor is only inferred from an exact supported model code', () => {
  assert.match(processorFor(makeOffer('s23', 300000, { model_code: 'SM-S911U1' })), /Snapdragon 8 Gen 2/);
  assert.equal(processorFor(makeOffer('s23_fe', 300000, { model_code: 'SM-S711B' })), 'Exynos 2200');
  assert.equal(processorFor(makeOffer('s23_fe', 300000, { model_code: 'SM-S711U1' })), 'Snapdragon 8 Gen 1');
  assert.match(processorFor(makeOffer('s23_fe', 300000)), /Por confirmar/);
});

test('price comparison exposes the actual price gap instead of an arbitrary winner', () => {
  const comparison = compareModelPrices([
    makeOffer('s23', 320000),
    makeOffer('s23_fe', 300000),
  ]);
  assert.equal(comparison.s23.price, 320000);
  assert.equal(comparison.s23_fe.price, 300000);
  assert.equal(comparison.s23Premium, 20000);
  assert.equal(compareModelPrices([]).s23, null);
});
