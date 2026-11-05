const $ = (id) => document.getElementById(id);
let profile = {};
let result = null;
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s]));
function showPane(name) {
  document.querySelectorAll('.pane').forEach(p => p.classList.toggle('active', p.id === 'pane-'+name));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.pane === name));
  window.scrollTo({top: 0, behavior: 'smooth'});
}
document.querySelectorAll('.nav-item').forEach(btn => btn.addEventListener('click', () => showPane(btn.dataset.pane)));
function notifyError(target, message) { $(target).innerHTML = `<div class="error">${esc(message)}</div>`; }
$('profile-form').addEventListener('submit', async e => {
  e.preventDefault();
  const btn=$('match-btn'); btn.disabled=true;btn.innerHTML='Matching your business…';
  profile = Object.fromEntries(new FormData(e.currentTarget));
  try {
    const res=await fetch('/api/recommend',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(profile)});
    const data=await res.json();if(!res.ok)throw Error(data.error||'Could not match tools');
    result=data;showResults(data);showRoadmap(data);showPane('results');
  }catch(err){showPane('results');notifyError('results-content',err.message)}
  finally{btn.disabled=false;btn.innerHTML='Find my AI solutions <span>→</span>'}
});
function showResults(data){
 const cards=data.tools.map((t,i)=>`<article class="tool-card">
    <div><div class="tool-type">${i===0?'✦ TOP DEMO MATCH · ':''}${esc(t.category)}</div><div class="tool-name">${esc(t.name)}</div></div>
    <p class="tool-desc">${esc(t.description)}</p>
    <div><div class="fit-score"><strong>${esc(t.score)}<span style="font-size:13px;color:#9ca9b9">/100</span></strong><small>illustrative match</small></div><div class="meter"><div style="width:${Math.min(100,Math.max(0,t.score))}%"></div></div></div>
    <ul class="reasons">${t.reasons.map(r=>`<li>${esc(r)}</li>`).join('')}</ul>
    <p class="pricing-note">Pricing: ${esc(t.pricing)}</p>
    <a class="tool-link" href="${esc(t.site)}" target="_blank" rel="noopener noreferrer">Check vendor information ↗</a>
  </article>`).join('');
 $('results-content').innerHTML=`<div class="solution-summary"><b>Recommended approach:</b> ${esc(data.solution)}<br><span class="captionish">These are prototype rankings, not verified performance tests.</span></div><div class="results-grid">${cards}</div><p class="fineprint">${esc(data.disclaimer)}</p>`;
}
function showRoadmap(data) {
 let tool = data.tools[0];
 $('roadmap-content').innerHTML=`<h3>Suggested next steps for ${esc(profile.business || 'your business')}</h3><p class="captionish">Candidate starting point: ${esc(tool.name)}. Vendor plans, integration requirements, and privacy terms need review.</p><div class="roadmap">${data.steps.map((s,i)=>`<label class="road-step"><input style="width:20px;min-height:20px;margin-top:4px" type="checkbox"><span class="road-number">${i+1}</span><div><b>${esc(s)}</b><p>Check off after you have completed and verified this step.</p></div></label>`).join('')}</div><p class="fineprint">Pilot with synthetic or anonymized data. Seek consent before marketing messages; avoid uploading sensitive customer records to unapproved AI services.</p>`;
}
$('upload-form').addEventListener('submit',async e => {
 e.preventDefault();const file=$('csv-file').files[0];if(!file){notifyError('analysis-results','Select a CSV first.');return}
 $('analysis-results').textContent='Analyzing…';
 try{const payload=new FormData();payload.append('file',file);const r=await fetch('/api/analyze',{method:'POST',body:payload});const d=await r.json();if(!r.ok)throw Error(d.error);
 $('analysis-results').innerHTML=`<div class="stats"><div class="stat"><strong>${d.customers}</strong><small>Unique customers</small></div><div class="stat"><strong>${d.orders}</strong><small>Orders</small></div><div class="stat"><strong>${d.repeat_rate}%</strong><small>Repeat purchasers</small></div></div><p style="margin-top:12px;color:#66758c">${esc(d.insight)}</p><p class="fineprint">Synthetic or user-supplied input; this result does not automatically prove that any AI tool will improve customer retention.</p>`;
 }catch(err){notifyError('analysis-results',err.message)}
});
$('preview-btn').addEventListener('click',async ()=>{
 let btn=$('preview-btn');btn.disabled=true;btn.textContent='Generating…';
 try{const r=await fetch('/api/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({business:profile.business||$('business').value})});const d=await r.json();if(!r.ok)throw Error(d.error||'Failed');$('preview-content').textContent=d.content+'\n\n['+d.mode+'] '+d.note;}catch(err){$('preview-content').textContent='Preview unavailable: '+err.message}finally{btn.disabled=false;btn.textContent='Generate sample email ✧'}
});
function calcRoi(){let hours=Math.max(0,Number($('hours').value)||0),rate=Math.max(0,Number($('hourly').value)||0),cost=Math.max(0,Number($('cost').value)||0);let net=(hours*rate-cost);$('roi-result').textContent=(net<0?'-':'')+'$'+Math.abs(net).toLocaleString(undefined,{maximumFractionDigits:2});$('roi-context').textContent=`${hours} hours × $${rate}/hour − $${cost}/month (assumptions)`}
['hours','hourly','cost'].forEach(id=>$(id).addEventListener('input',calcRoi));calcRoi();
$('discover-form').addEventListener('submit',async e=>{
 e.preventDefault();$('discovery-results').textContent='Retrieving the official page…';
 try{const r=await fetch('/api/discover',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:$('discover-url').value})});const d=await r.json();if(!r.ok)throw Error(d.error||'Discovery failed');$('discovery-results').innerHTML=`<div class="discovery-card"><span class="status">${esc(d.status)}</span><b>${esc(d.title)}</b><p>${esc(d.summary)}</p><a href="${esc(d.source)}" target="_blank" rel="noopener noreferrer">View source ↗</a><p class="fineprint">Extraction method: ${esc(d.mode)}. ${esc(d.note)}</p></div>`;}catch(err){notifyError('discovery-results',err.message)}
});
