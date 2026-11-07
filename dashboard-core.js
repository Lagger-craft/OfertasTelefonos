(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.S23PriceCore = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const WITHIN_BUDGET_MAX = 340000;
  const NEAR_BUDGET_MAX = 370000;
  const MODELS = ['s23', 's23_fe'];
  const HISTOGRAM_BINS = [
    { min: 0, max: 250000, label: 'Hasta $250k' },
    { min: 250001, max: 280000, label: '$250k–$280k' },
    { min: 280001, max: 310000, label: '$280k–$310k' },
    { min: 310001, max: 340000, label: '$310k–$340k' },
    { min: 340001, max: 370000, label: '$340k–$370k' },
  ];

  function priceBand(price) {
    if (!Number.isSafeInteger(price) || price <= 0) return 'excluded';
    if (price <= WITHIN_BUDGET_MAX) return 'within_budget';
    if (price <= NEAR_BUDGET_MAX) return 'near_budget';
    return 'excluded';
  }

  function validSnapshots(history) {
    const snapshots = history && history.snapshots;
    if (!snapshots || typeof snapshots !== 'object' || Array.isArray(snapshots)) return [];
    return Object.entries(snapshots)
      .filter(([, snapshot]) => snapshot && Array.isArray(snapshot.offers))
      .sort(([weekA], [weekB]) => weekA.localeCompare(weekB));
  }

  function latestSnapshot(history) {
    const snapshots = validSnapshots(history);
    if (!snapshots.length) return null;
    const [week, snapshot] = snapshots[snapshots.length - 1];
    return { week, ...snapshot };
  }

  function isTrackableOffer(offer) {
    return offer && MODELS.includes(offer.model) &&
      priceBand(offer.price) !== 'excluded' && offer.available !== false;
  }

  function currentOffers(history) {
    const snapshot = latestSnapshot(history);
    if (!snapshot) return [];
    return snapshot.offers.filter(isTrackableOffer)
      .map((offer) => ({ ...offer, band: priceBand(offer.price) }))
      .sort((a, b) => MODELS.indexOf(a.model) - MODELS.indexOf(b.model) ||
        a.price - b.price || String(a.title || '').localeCompare(String(b.title || '')));
  }

  function buildPriceHistogram(offers) {
    return HISTOGRAM_BINS.map((bin) => ({
      ...bin,
      count: (Array.isArray(offers) ? offers : []).filter((offer) =>
        isTrackableOffer(offer) && offer.price >= bin.min && offer.price <= bin.max).length,
    }));
  }

  function buildPriceTrend(history) {
    const snapshots = validSnapshots(history);
    const series = Object.fromEntries(MODELS.map((model) => [model, []]));
    const weeks = [];
    for (const [week, snapshot] of snapshots) {
      weeks.push(week);
      for (const model of MODELS) {
        const prices = snapshot.offers.filter((offer) =>
          isTrackableOffer(offer) && offer.model === model).map((offer) => offer.price);
        series[model].push(prices.length ? Math.min(...prices) : null);
      }
    }
    return { weeks, series };
  }

  function processorFor(offer) {
    if (offer && typeof offer.processor === 'string' &&
        offer.processor !== 'unknown' && offer.processor.trim()) return offer.processor;
    if (!offer) return 'Por confirmar: revisa el código de modelo';
    if (offer.model === 's23') return 'Snapdragon 8 Gen 2 for Galaxy';
    const code = String(offer.model_code || '').toUpperCase();
    if (code.startsWith('SM-S711B')) return 'Exynos 2200';
    if (code.startsWith('SM-S711U')) return 'Snapdragon 8 Gen 1';
    return 'Por confirmar: revisa el código de modelo';
  }

  function cheapestOffer(offers, model) {
    return (Array.isArray(offers) ? offers : [])
      .filter((offer) => isTrackableOffer(offer) && offer.model === model)
      .reduce((best, offer) => !best || offer.price < best.price ? offer : best, null);
  }

  function compareModelPrices(offers) {
    const s23 = cheapestOffer(offers, 's23');
    const s23_fe = cheapestOffer(offers, 's23_fe');
    return {
      s23,
      s23_fe,
      s23Premium: s23 && s23_fe ? s23.price - s23_fe.price : null,
    };
  }

  function formatCLP(value) {
    if (!Number.isSafeInteger(value)) return '—';
    return new Intl.NumberFormat('es-CL', {
      style: 'currency', currency: 'CLP', maximumFractionDigits: 0,
    }).format(value);
  }

  return {
    WITHIN_BUDGET_MAX,
    NEAR_BUDGET_MAX,
    priceBand,
    latestSnapshot,
    currentOffers,
    buildPriceHistogram,
    buildPriceTrend,
    processorFor,
    compareModelPrices,
    formatCLP,
  };
});
