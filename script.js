const tabs = [...document.querySelectorAll('[role="tab"]')];
const panels = [...document.querySelectorAll('[role="tabpanel"]')];
const core = window.S23PriceCore;
const SVG_NS = 'http://www.w3.org/2000/svg';

function activateTab(nextTab, moveFocus = false) {
  const model = nextTab.dataset.model;

  tabs.forEach((tab) => {
    const selected = tab === nextTab;
    tab.classList.toggle('is-active', selected);
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
  });

  panels.forEach((panel) => {
    const selected = panel.dataset.panel === model;
    panel.hidden = !selected;
    panel.classList.toggle('is-active', selected);
  });

  if (moveFocus) nextTab.focus();
}

tabs.forEach((tab, index) => {
  tab.addEventListener('click', () => activateTab(tab));
  tab.addEventListener('keydown', (event) => {
    let nextIndex;
    if (event.key === 'ArrowRight') nextIndex = (index + 1) % tabs.length;
    if (event.key === 'ArrowLeft') nextIndex = (index - 1 + tabs.length) % tabs.length;
    if (event.key === 'Home') nextIndex = 0;
    if (event.key === 'End') nextIndex = tabs.length - 1;
    if (nextIndex !== undefined) {
      event.preventDefault();
      activateTab(tabs[nextIndex], true);
    }
  });
});

function byId(id) {
  return document.getElementById(id);
}

function setText(id, value) {
  const node = byId(id);
  if (node) node.textContent = value;
}

function sourceLabel(source) {
  const host = new URL(source).hostname.toLowerCase();
  if (host.includes('reuse')) return 'Reuse Chile';
  if (host.includes('bodegaoportunidades')) return 'Bodega Oportunidades';
  if (host.includes('dlphone')) return 'DL Phone';
  if (host.includes('comprala')) return 'Comprala';
  return host;
}

function safeOfferUrl(offer) {
  if (!offer || typeof offer.url !== 'string') return null;
  try {
    const target = new URL(offer.url);
    const sourceHost = new URL(offer.source).hostname.toLowerCase();
    const allowed = sourceHost === 'reusecl.myshopify.com'
      ? ['reusecl.myshopify.com', 'reuse.cl', 'www.reuse.cl']
      : sourceHost === 'bogedaoportunidades.myshopify.com'
        ? ['bogedaoportunidades.myshopify.com', 'bodegaoportunidades.cl', 'www.bodegaoportunidades.cl']
        : sourceHost === 'dlphone.cl'
          ? ['dlphone.cl', 'www.dlphone.cl', 'd8f7f8-76.myshopify.com']
          : sourceHost === 'comprala.cl'
            ? ['comprala.cl', 'www.comprala.cl', 'comprala-cl.myshopify.com']
            : [sourceHost];
    if (target.protocol !== 'https:' || !allowed.includes(target.hostname.toLowerCase()) ||
        target.username || target.password || target.port) return null;
    return target.href;
  } catch {
    return null;
  }
}

function offerModelLabel(model) {
  if (model === 's23_fe') return 'Galaxy S23 FE';
  if (model === 's23_plus') return 'Galaxy S23+';
  if (model === 's24_fe') return 'Galaxy S24 FE';
  return 'Galaxy S23';
}

function makeCell(label) {
  const cell = document.createElement('td');
  cell.dataset.label = label;
  return cell;
}

function createOfferRow(offer, index, budget) {
  const row = document.createElement('tr');
  if (index === 0) row.classList.add('best-deal');

  const storeCell = makeCell('Tienda');
  const store = document.createElement('span');
  store.className = 'store-name';
  store.textContent = sourceLabel(offer.source);
  const listing = document.createElement('span');
  listing.className = 'store-sub';
  listing.textContent = offer.title || offerModelLabel(offer.model);
  const processor = document.createElement('span');
  processor.className = 'store-sub processor-label';
  processor.textContent = core.processorFor(offer);
  storeCell.append(store, listing, processor);

  const priceCell = makeCell('Precio');
  const price = document.createElement('span');
  price.className = 'price';
  price.textContent = core.formatCLP(offer.price);
  const caps = budget || { within_max: core.WITHIN_BUDGET_MAX, near_max: core.NEAR_BUDGET_MAX };
  const tag = document.createElement('span');
  tag.className = offer.band === 'near_budget' ? 'price-tag price-tag-warn' : 'price-tag';
  tag.textContent = offer.band === 'near_budget'
    ? `CERCA DEL TOPE · +${core.formatCLP(offer.price - caps.within_max)}`
    : index === 0 ? 'MEJOR PRECIO EN ESTA LISTA' : 'DENTRO DEL TOPE';
  priceCell.append(price, tag);

  const detailCell = makeCell('Detalle');
  const model = document.createElement('span');
  model.className = 'offer-model';
  model.textContent = `${offerModelLabel(offer.model)} · ${offer.model_code || 'código por confirmar'}`;
  const availability = document.createElement('span');
  availability.className = 'store-sub';
  availability.textContent = offer.available === true
    ? 'Stock informado en la última consulta'
    : 'Disponibilidad no informada; confirma con la tienda';
  const battery = document.createElement('span');
  battery.className = 'store-sub';
  battery.textContent = 'Salud de batería no informada en la ficha';
  const updatesMonths = core.updateMonthsLeft(offer.model);
  const supportMeta = core.MODEL_UPDATE_SUPPORT[offer.model];
  if (supportMeta && updatesMonths !== null) {
    const updates = document.createElement('span');
    updates.className = 'store-sub';
    const untilDate = new Date(`${supportMeta.security_until}-01T00:00:00`);
    const untilLabel = new Intl.DateTimeFormat('es-CL', {
      year: 'numeric', month: 'short', timeZone: 'America/Santiago',
    }).format(untilDate);
    updates.textContent = updatesMonths > 0
      ? `Actualizaciones de seguridad: hasta ${untilLabel} (${updatesMonths} ${updatesMonths === 1 ? 'mes' : 'meses'})`
      : 'Ventana de seguridad del fabricante finalizada';
    detailCell.append(model, availability, updates, battery);
  } else {
    detailCell.append(model, availability, battery);
  }

  const linkCell = makeCell('Enlace');
  const href = safeOfferUrl(offer);
  if (href) {
    const link = document.createElement('a');
    link.className = 'link-button';
    link.href = href;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.append(document.createTextNode('VER FICHA '));
    const arrow = document.createElement('span');
    arrow.setAttribute('aria-hidden', 'true');
    arrow.textContent = '↗';
    link.append(arrow);
    linkCell.append(link);
  } else {
    linkCell.textContent = 'Enlace no disponible';
  }

  row.append(storeCell, priceCell, detailCell, linkCell);
  return row;
}

const OFFER_TABLE_IDS = {
  s23_fe: 'offers-fe',
  s23: 'offers-s23',
  s23_plus: 'offers-s23-plus',
  s24_fe: 'offers-s24-fe',
};

function renderOfferTable(model, offers, budget) {
  const tbody = byId(OFFER_TABLE_IDS[model] || '');
  if (!tbody) return;
  tbody.replaceChildren();
  const matching = offers.filter((offer) => offer.model === model);
  if (!matching.length) {
    const row = document.createElement('tr');
    row.className = 'empty-row';
    const cell = document.createElement('td');
    cell.colSpan = 4;
    const caps = budget || { within_max: core.WITHIN_BUDGET_MAX, near_max: core.NEAR_BUDGET_MAX };
    cell.textContent = `No hay ofertas reacondicionadas debajo del tope configurado (${core.formatCLP(caps.near_max)}) en la última captura.`;
    row.append(cell);
    tbody.append(row);
    return;
  }
  matching.forEach((offer, index) => tbody.append(createOfferRow(offer, index, budget)));
}

function formatSnapshotDate(value) {
  if (typeof value !== 'string') return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat('es-CL', {
    dateStyle: 'medium', timeStyle: 'short', timeZone: 'America/Santiago',
  }).format(date);
}

const COUNT_IDS = {
  s23_fe: 'count-fe',
  s23: 'count-s23',
  s23_plus: 'count-s23-plus',
  s24_fe: 'count-s24-fe',
};

function renderPriceSummary(history, offers, budget) {
  const comparison = core.compareModelPrices(offers);
  for (const key of ['s23_fe', 's23', 's23_plus', 's24_fe']) {
    const bestId = key === 's23_fe' ? 'best-fe-price' : `best-${key.replace('_', '-')}-price`;
    setText(bestId, comparison[key] ? core.formatCLP(comparison[key].price) : '—');
    setText(COUNT_IDS[key], String(offers.filter((offer) => offer.model === key).length).padStart(2, '0'));
  }
  const caps = budget || core.budgetFor(history);
  setText('strip-within', core.formatCLP(caps.within_max));
  setText('strip-near', core.formatCLP(caps.near_max));

  const snapshot = core.latestSnapshot(history);
  if (!snapshot) {
    setText('price-update-status', 'Sin capturas semanales todavía.');
    return;
  }
  const collectedAt = formatSnapshotDate(history.last_collected_at || snapshot.collected_at);
  const failedSources = Object.values(snapshot.sources || {}).filter((status) =>
    typeof status === 'string' && status.startsWith('error:')).length;
  const freshness = collectedAt ? `Última captura: ${collectedAt}` : `Última semana: ${snapshot.week}`;
  setText('price-update-status', failedSources
    ? `${freshness} · ${failedSources} fuente(s) no respondieron`
    : freshness);
}

function securityUntilLabel(securityUntil) {
  const date = new Date(`${securityUntil}-01T00:00:00`);
  return new Intl.DateTimeFormat('es-CL', {
    year: 'numeric', month: 'long', timeZone: 'America/Santiago',
  }).format(date);
}

function renderModelComparison(comparison) {
  const tbody = byId('model-comparison-body');
  if (!tbody) return;
  tbody.replaceChildren();
  for (const model of core.MODELS_ORDER || ['s23_fe', 's23', 's23_plus', 's24_fe']) {
    const spec = (core.MODEL_SPECS || {})[model];
    if (!spec) continue;
    const row = document.createElement('tr');
    const modelCell = makeCell('Modelo');
    const name = document.createElement('span');
    name.className = 'comparison-model';
    name.textContent = offerModelLabel(model);
    modelCell.append(name);
    const chipCell = makeCell('Procesador');
    const chip = document.createElement('span');
    chip.className = 'comparison-chip';
    chip.textContent = spec.chip_variants.map((variant) => variant.chip).join(' · ');
    chipCell.append(chip);
    if (spec.chip_variants.some((variant) => variant.region !== 'todas las regiones')) {
      const variants = document.createElement('span');
      variants.className = 'store-sub';
      variants.textContent = spec.chip_variants
        .map((variant) => `${variant.chip} (${variant.region})`)
        .join(' · ');
      chipCell.append(variants);
    }
    const batteryCell = makeCell('Batería');
    const battery = document.createElement('span');
    battery.className = 'comparison-battery';
    battery.textContent = `${new Intl.NumberFormat('es-CL').format(spec.battery_mah)} mAh`;
    batteryCell.append(battery);
    const supportCell = makeCell('Seguridad hasta');
    const support = document.createElement('span');
    support.className = 'comparison-support';
    support.textContent = securityUntilLabel(spec.security_until);
    supportCell.append(support);
    const priceCell = makeCell('Mejor precio actual');
    const best = comparison && comparison[model];
    if (best) {
      const price = document.createElement('span');
      price.className = 'price green-text';
      price.textContent = core.formatCLP(best.price);
      priceCell.append(price);
    } else {
      priceCell.textContent = 'Sin ofertas en la última captura';
    }
    row.append(modelCell, chipCell, batteryCell, supportCell, priceCell);
    tbody.append(row);
  }
}

function renderPriceAdvice(offers) {
  const comparison = core.compareModelPrices(offers);
  const { s23, s23_fe: fe, s23Premium } = comparison;
  const premiumPair = comparison.s23_plus;
  const fe24 = comparison.s24_fe;
  if (!s23 && !fe && !premiumPair && !fe24) {
    setText('price-advice', 'Cuando haya precios actuales, compararemos el menor de cada modelo. En general, el S23 y el S23+ favorecen potencia y eficiencia; el S23 FE y el S24 FE pueden convenir si cuestan bastante menos y su batería está en buen estado.');
    return;
  }
  const bestParts = [premiumPair, s23, fe, fe24]
    .filter(Boolean)
    .sort((a, b) => a.price - b.price)
    .map((offer) => `${offerModelLabel(offer.model)} a ${core.formatCLP(offer.price)}`);
  let text = `Mejores precios de la última captura: ${bestParts.join('; ')}.`;
  if (s23 && fe) {
    const difference = Math.abs(s23Premium);
    const relation = s23Premium > 0 ? 'más caro' : s23Premium < 0 ? 'más barato' : 'al mismo precio';
    text += ` El S23 está ${relation} que el S23 FE por ${core.formatCLP(difference)}: la plataforma más potente compensa si la diferencia es chica, mientras que el FE puede ser mejor valor con batería saludable.`;
  }
  setText('price-advice', text);
}

function svgElement(name, attributes = {}, text = null) {
  const element = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
  if (text !== null) element.textContent = text;
  return element;
}

function resetSvg(svg, titleId, descriptionId, description) {
  const title = document.createElementNS(SVG_NS, 'title');
  title.id = titleId;
  title.textContent = svg.querySelector(`#${titleId}`)?.textContent || '';
  const desc = document.createElementNS(SVG_NS, 'desc');
  desc.id = descriptionId;
  desc.textContent = description;
  svg.replaceChildren(title, desc);
}

function drawTrend(history) {
  const svg = byId('trend-chart');
  const empty = byId('trend-empty');
  if (!svg || !empty) return;
  const trend = core.buildPriceTrend(history);
  const allValues = [...trend.series.s23, ...trend.series.s23_fe,
    ...trend.series.s23_plus, ...trend.series.s24_fe].filter(Number.isFinite);
  if (!trend.weeks.length || !allValues.length) {
    svg.hidden = true;
    empty.hidden = false;
    return;
  }
  svg.hidden = false;
  empty.hidden = true;
  resetSvg(svg, 'trend-svg-title', 'trend-svg-desc',
    `Menor precio semanal observado para Galaxy S23, S23+, S23 FE y S24 FE durante ${trend.weeks.length} semanas. Valores en pesos chilenos.`);

  const width = 720;
  const height = 280;
  const plot = { left: 68, right: 20, top: 18, bottom: 48 };
  const plotWidth = width - plot.left - plot.right;
  const plotHeight = height - plot.top - plot.bottom;
  let min = Math.min(...allValues);
  let max = Math.max(...allValues);
  if (min === max) { min = Math.max(0, min - 15000); max += 15000; }
  const xFor = (index) => trend.weeks.length === 1
    ? plot.left + plotWidth / 2
    : plot.left + index * plotWidth / (trend.weeks.length - 1);
  const yFor = (value) => plot.top + (max - value) * plotHeight / (max - min);

  for (let tick = 0; tick <= 4; tick += 1) {
    const value = min + (max - min) * tick / 4;
    const y = yFor(value);
    svg.append(svgElement('line', { x1: plot.left, y1: y, x2: width - plot.right, y2: y, class: 'chart-gridline' }));
    svg.append(svgElement('text', { x: plot.left - 9, y: y + 4, 'text-anchor': 'end', class: 'chart-axis-label' },
      `$${Math.round(value / 1000)}k`));
  }

  const seriesInfo = [
    { key: 's23', className: 'chart-line-s23', pointClass: 'chart-point-s23' },
    { key: 's23_fe', className: 'chart-line-fe', pointClass: 'chart-point-fe' },
    { key: 's23_plus', className: 'chart-line-s23-plus', pointClass: 'chart-point-s23-plus' },
    { key: 's24_fe', className: 'chart-line-s24-fe', pointClass: 'chart-point-s24-fe' },
  ];
  for (const info of seriesInfo) {
    let segment = [];
    const flush = () => {
      if (segment.length > 1) {
        svg.append(svgElement('polyline', {
          points: segment.map(({ x, y }) => `${x},${y}`).join(' '),
          class: info.className,
        }));
      }
      for (const point of segment) {
        const circle = svgElement('circle', { cx: point.x, cy: point.y, r: 4, class: info.pointClass });
        circle.append(svgElement('title', {}, `${point.week}: ${core.formatCLP(point.value)}`));
        svg.append(circle);
      }
      segment = [];
    };
    trend.series[info.key].forEach((value, index) => {
      if (value === null || !Number.isFinite(value)) { flush(); return; }
      segment.push({ x: xFor(index), y: yFor(value), value, week: trend.weeks[index] });
    });
    flush();
  }

  const labelStep = Math.max(1, Math.ceil(trend.weeks.length / 6));
  trend.weeks.forEach((week, index) => {
    if (index % labelStep !== 0 && index !== trend.weeks.length - 1) return;
    svg.append(svgElement('text', {
      x: xFor(index), y: height - 15, 'text-anchor': 'middle', class: 'chart-axis-label',
    }, week));
  });
}

function drawHistogram(offers, budget) {
  const svg = byId('histogram-chart');
  const empty = byId('histogram-empty');
  if (!svg || !empty) return;
  const bins = core.buildPriceHistogram(offers, budget);
  const total = bins.reduce((sum, bin) => sum + bin.count, 0);
  if (!total) {
    svg.hidden = true;
    empty.hidden = false;
    return;
  }
  svg.hidden = false;
  empty.hidden = true;
  resetSvg(svg, 'histogram-svg-title', 'histogram-svg-desc',
    `Distribución de ${total} ofertas vigentes por rango de precio en pesos chilenos.`);

  const width = 560;
  const height = 280;
  const top = 22;
  const bottom = 52;
  const chartHeight = height - top - bottom;
  const maxCount = Math.max(...bins.map((bin) => bin.count), 1);
  const startX = 36;
  const step = 104;
  const barWidth = 62;
  bins.forEach((bin, index) => {
    const barHeight = bin.count ? bin.count * chartHeight / maxCount : 0;
    const x = startX + index * step;
    const y = top + chartHeight - barHeight;
    svg.append(svgElement('rect', {
      x, y, width: barWidth, height: Math.max(0, barHeight), rx: 3,
      class: index === bins.length - 1 ? 'histogram-bar histogram-bar-near' : 'histogram-bar',
    }));
    svg.append(svgElement('text', {
      x: x + barWidth / 2, y: y - 8, 'text-anchor': 'middle', class: 'chart-value-label',
    }, String(bin.count)));
    svg.append(svgElement('text', {
      x: x + barWidth / 2, y: height - 17, 'text-anchor': 'middle', class: 'chart-axis-label',
    }, bin.label));
  });
}

function renderDashboard(history) {
  if (!core) throw new Error('No se pudo cargar el módulo de precios.');
  const budget = core.budgetFor(history);
  const offers = core.currentOffers(history);
  renderOfferTable('s23_fe', offers, budget);
  renderOfferTable('s23', offers, budget);
  renderOfferTable('s23_plus', offers, budget);
  renderOfferTable('s24_fe', offers, budget);
  renderPriceSummary(history, offers, budget);
  renderPriceAdvice(offers);
  renderModelComparison(core.compareModelPrices(offers));
  drawTrend(history);
  drawHistogram(offers, budget);
}

async function loadPriceHistory() {
  try {
    const response = await fetch('data/price-history.json', { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderDashboard(await response.json());
  } catch {
    const onFileProtocol = window.location.protocol === 'file:';
    const hint = onFileProtocol
      ? 'Abrí la página con ./start-website.sh (o python3 -m http.server 8000): el navegador bloquea la lectura de datos cuando se abre index.html directo.'
      : 'No se pudo leer data/price-history.json. Ejecutá python3 scripts/update_prices.py para poblar el historial.';
    setText('price-update-status', hint);
    setText('trend-empty', hint);
    setText('histogram-empty', hint);
    setText('price-advice', 'Cuando el archivo de precios esté disponible, esta sección comparará los valores capturados y sus variantes de procesador.');
  }
}

async function runSearchNow() {
  const button = byId('run-search');
  const status = byId('run-search-status');
  if (!button || !status || button.disabled) return;
  button.disabled = true;
  status.textContent = 'Buscando ofertas en la última ronda de tiendas…';
  try {
    const response = await fetch('/api/run-search', { method: 'POST' });
    const result = await response.json();
    if (!result || typeof result.ok !== 'boolean') throw new Error('respuesta incompleta');
    if (result.ok) {
      status.textContent = `Búsqueda completada en ${result.duration_s} s. Actualizando la tabla…`;
      await loadPriceHistory();
      status.textContent = `Búsqueda completada en ${result.duration_s} s. La tabla abajo ya muestra la captura más reciente.`;
    } else {
      status.textContent = `La búsqueda falló (${result.exit_code == null ? 'sin respuesta del collector' : `código ${result.exit_code}`}). Detalle: ${result.tail || 'sin salida'}`;
    }
  } catch {
    const onFileProtocol = window.location.protocol === 'file:';
    status.textContent = onFileProtocol
      ? 'Abri la web con ./start-website.sh: el buscador de la página sólo funciona servida en local (http://localhost:8000).'
      : 'No se pudo ejecutar la búsqueda. Confirmá que el servidor local (start-website.sh) esté corriendo.';
  } finally {
    button.disabled = false;
  }
}

const runSearchButton = byId('run-search');
if (runSearchButton) runSearchButton.addEventListener('click', runSearchNow);

loadPriceHistory();
