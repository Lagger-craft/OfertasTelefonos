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
  budgetFor,
  MODEL_UPDATE_SUPPORT,
  MODEL_SPECS,
  updateMonthsLeft,
} = require('../dashboard-core.js');

const makeOffer = (model, price, extra = {}) => ({
  key: `${model}-${price}`,
  model,
  price,
  currency: 'CLP',
  available: true,
  ...extra,
});

const history = (snapshots, budget = { currency: 'CLP', within_max: 340000, near_max: 370000 }) => ({
  schema_version: 1,
  budget,
  last_collected_at: '2026-02-16T12:00:00+00:00',
  snapshots,
});

const EXPECTED_SPEC_BATTERY = {
  s23: 3900,
  s23_plus: 4700,
  s23_fe: 4500,
  s24_fe: 4700,
};

test('every tracked model has a spec sheet with chip and battery data', () => {
  for (const key of ['s23', 's23_fe', 's23_plus', 's24_fe']) {
    const spec = MODEL_SPECS[key];
    assert.equal(typeof spec, 'object', `missing spec for ${key}`);
    assert.equal(spec.battery_mah, EXPECTED_SPEC_BATTERY[key], `battery for ${key}`);
    assert.equal(typeof spec.chip_label, 'string');
    assert.ok(Array.isArray(spec.chip_variants) && spec.chip_variants.length >= 1,
      `chip variants for ${key}`);
    for (const variant of spec.chip_variants) {
      assert.equal(typeof variant.chip, 'string');
      assert.equal(typeof variant.region, 'string');
    }
    // The security window lives in one place: specs must mirror the support table.
    assert.equal(spec.security_until, MODEL_UPDATE_SUPPORT[key].security_until);
  }
});

test('s23 fe spec names both regional chip variants', () => {
  const chips = MODEL_SPECS.s23_fe.chip_variants.map((variant) => variant.chip);
  assert.ok(chips.includes('Exynos 2200'));
  assert.ok(chips.includes('Snapdragon 8 Gen 1'));
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

test('budgetFor reads user caps from history and falls back to defaults', () => {
  assert.equal(budgetFor(history({})).within_max, 340000);
  assert.equal(budgetFor(history({})).near_max, 370000);
  const custom = history({
    '2026-W07': { offers: [] },
  }, { currency: 'CLP', within_max: 300000, near_max: 320000 });
  assert.deepEqual(budgetFor(custom), { within_max: 300000, near_max: 320000 });
});

test('priceBand honors explicit caps', () => {
  assert.equal(priceBand(300000, 300000, 320000), 'within_budget');
  assert.equal(priceBand(310000, 300000, 320000), 'near_budget');
  assert.equal(priceBand(320001, 300000, 320000), 'excluded');
});

test('current offers honor user caps stored in history', () => {
  const custom = history({
    '2026-W07': { offers: [
      makeOffer('s23', 310000),
      makeOffer('s23', 320001),
      makeOffer('s23_plus', 250000),
    ] },
  }, { currency: 'CLP', within_max: 300000, near_max: 320000 });
  assert.deepEqual(
    currentOffers(custom).map((offer) => [offer.price, offer.band]),
    [[310000, 'near_budget'], [250000, 'within_budget']]);
});

test('price comparison covers all four tracked models', () => {
  const comparison = compareModelPrices([
    makeOffer('s23', 320000),
    makeOffer('s23_fe', 300000),
    makeOffer('s23_plus', 360000),
    makeOffer('s24_fe', 280000),
  ]);
  assert.equal(comparison.s23_plus.price, 360000);
  assert.equal(comparison.s24_fe.price, 280000);
});

test('processor maps the new models from deterministic spec', () => {
  assert.match(processorFor(makeOffer('s23_plus', 300000)), /Snapdragon 8 Gen 2/);
  assert.equal(processorFor(makeOffer('s24_fe', 300000, { model_code: 'SM-S721B' })), 'Exynos 2400e');
  assert.match(processorFor(makeOffer('s24_fe', 300000)), /Exynos 2400e/);
});

test('custom budget bands look trackable: histogram honors them', () => {
  const custom = history({
    '2026-W07': { offers: [makeOffer('s23', 200000), makeOffer('s23', 400000)] },
  }, { currency: 'CLP', within_max: 300000, near_max: 320000 });
  const trend = buildPriceTrend(custom);
  assert.deepEqual(trend.series.s23, [200000]);
});

test('manufacturer support metadata exists for every tracked model', () => {
  for (const model of ['s23', 's23_fe', 's23_plus', 's24_fe']) {
    const meta = MODEL_UPDATE_SUPPORT[model];
    assert.ok(meta, model);
    assert.match(meta.security_until, /^\d{4}-\d{2}$/);
    assert.ok(meta.support_pledge, model);
  }
});

test('update months left counts whole months and clamps to zero', () => {
  assert.equal(updateMonthsLeft('s23', new Date('2026-11-07T00:00:00Z')), 15);
  assert.equal(updateMonthsLeft('s24_fe', new Date('2026-11-07T00:00:00Z')), 58);
  assert.equal(updateMonthsLeft('s23', new Date('2029-06-01T00:00:00Z')), 0);
  assert.equal(updateMonthsLeft('unknown_model', new Date()), null);
  // 'now' is optional and module-wide controllable by the caller.
  assert.equal(updateMonthsLeft('s23'), updateMonthsLeft('s23', new Date()));
});
