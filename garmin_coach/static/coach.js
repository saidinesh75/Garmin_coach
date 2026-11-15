'use strict';
(() => {
  let working = false, sequence = 0, renderedEnd = '';
  const buttons = ['coach-daily', 'coach-weekly', 'coach-ask'];
  function selectedEnd() { return $('to').value || statusData?.today || ''; }
  function updateRange() {
    const end = selectedEnd();
    $('coach-export').href = '/api/coach/export?' + new URLSearchParams({end});
    if (renderedEnd && renderedEnd !== end) {
      $('coach-result').replaceChildren(el('p', 'Date range changed. Generate a report for the selected end date.', 'muted'));
      renderedEnd = '';
    }
  }
  async function check() {
    try {
      const info = await api('/api/coach/status');
      if (!working) $('ai-mode').textContent = info.message;
    } catch (e) { if (!working) $('ai-mode').textContent = e.message; }
  }
  function show(result) {
    const box = $('coach-result'); box.replaceChildren();
    $('ai-mode').textContent = result.message;
    if (!result.report) return;
    const evidence = result.evidence;
    box.append(el('p', `${result.kind === 'weekly' ? 'Weekly review' : 'Daily briefing / question'} · through ${evidence.anchor} · ${result.cached ? 'saved result' : 'generated locally'} · ${result.generated_at}`, 'muted'));
    if (evidence.historical || evidence.stale) box.append(el('p', 'Historical or stale evidence: this report cannot establish how you are doing today.', 'action'));
    for (const [key, title] of [['observations', 'What stands out'], ['suggestions', 'Possible next steps']]) {
      if (!result.report[key].length) continue;
      const card = el('article', undefined, 'insight-card'); card.append(el('h3', title));
      for (const item of result.report[key]) {
        card.append(el('p', item.text));
        for (const id of item.evidence_ids) {
          const fact = evidence.facts[id];
          const detail = el('details'); detail.append(el('summary', fact.label + ' · evidence'));
          if (id === 'training') {
            detail.append(el('p', `${fact.recent_count} recorded activities · ${fact.recent_minutes} min; preceding week ${fact.previous_minutes} min. All ${fact.expected_days} activity dates checked.`));
          } else {
            detail.append(el('p', `${fact.recent_start} to ${fact.recent_end}: mean ${fact.current} ${fact.unit} (${fact.recent_n}/7 days). Baseline ${fact.baseline_start} to ${fact.baseline_end}: median ${fact.baseline} ${fact.unit} (${fact.baseline_n}/28 days).`));
          }
          card.append(detail);
        }
      }
      box.append(card);
    }
    box.append(el('p', result.report.question, 'coach-followup'), el('p', result.message, 'muted'));
  }
  async function generate(kind, question = '') {
    if (working) return;
    const end = selectedEnd();
    if (!end) { $('ai-mode').textContent = 'Wait for the dashboard to load.'; return; }
    const thisSequence = ++sequence;
    working = true; buttons.forEach(id => $(id).disabled = true);
    $('ai-mode').textContent = 'Generating locally with Qwen3… The first load can take longer. No paid API is used.';
    try {
      const result = await api('/api/coach', {method: 'POST', body: JSON.stringify({
        end, kind, question, energy: $('coach-energy').value,
        soreness: $('coach-soreness').value, notes: $('coach-notes').value
      })});
      if (thisSequence === sequence && end === selectedEnd()) { show(result); renderedEnd = end; }
      else $('ai-mode').textContent = 'The selected date changed. Generate a new report.';
    } catch (e) { $('ai-mode').textContent = e.message; }
    finally { working = false; buttons.forEach(id => $(id).disabled = false); }
  }
  $('coach-daily').onclick = () => generate('daily');
  $('coach-weekly').onclick = () => generate('weekly');
  $('coach-form').onsubmit = event => { event.preventDefault(); const question = $('coach-question').value.trim(); if (question) generate('daily', question); };
  $('to').addEventListener('change', updateRange);
  $('coach-export').addEventListener('click', updateRange);
  // Status checks do not load the model, generate text or download anything.
  check();
})();
