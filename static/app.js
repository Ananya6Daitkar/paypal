const $ = (selector) => document.querySelector(selector);
const itemsEl = $('#items');
const totalEl = $('#total');
let analysis = null;
let config = null;
const flow = ['intent', 'review', 'approve', 'verify'];

function setFlow(stage) {
  const active = flow.indexOf(stage);
  flow.forEach((id, i) => {
    const element = $(`#flow-${id}`);
    element.classList.toggle('complete', i < active);
    element.classList.toggle('active', i === active);
  });
  const labels = {intent:'Awaiting your review',review:'Review your cart',approve:'Ready for your approval',verify:'Checking PayPal order'};
  $('#ticket-state').textContent = labels[stage] || stage;
}

function addItem(name = '', price = '', quantity = 1) {
  const row = document.createElement('div');
  row.className = 'item';
  row.innerHTML = '<input class="name" aria-label="Item name" placeholder="Item name" maxlength="100"><div class="pricewrap"><input class="price" aria-label="Unit price in dollars" placeholder="0.00" inputmode="decimal"></div><input class="qty" aria-label="Quantity" type="number" min="1" max="20" value="1"><button class="remove" aria-label="Remove item">×</button>';
  row.querySelector('.name').value = name;
  row.querySelector('.price').value = price;
  row.querySelector('.qty').value = quantity;
  row.addEventListener('input', () => { updateTotal(); markStale(); });
  row.querySelector('.remove').onclick = () => { row.remove(); updateTotal(); markStale(); };
  itemsEl.append(row);
  updateTotal();
}

function cart() {
  return [...itemsEl.querySelectorAll('.item')].map((row) => ({
    name: row.querySelector('.name').value,
    price: row.querySelector('.price').value,
    quantity: row.querySelector('.qty').value,
  }));
}

function updateTotal() {
  const value = cart().reduce((sum, item) => sum + (Number(item.price) || 0) * (Number(item.quantity) || 0), 0);
  totalEl.textContent = `$${value.toFixed(2)}`;
}

function disablePayment(message) {
  $('#paypal-button-container').replaceChildren();
  $('#payhint').textContent = message;
}

function status(message) { $('#paymentstatus').textContent = message; }

function markStale() {
  const hadReview = Boolean(analysis) || !$('#result').hidden;
  analysis = null;
  disablePayment('Cart or intent changed. Review it again before checkout.');
  if (!hadReview) return;
  $('#result').hidden = true;
  $('#empty').hidden = false;
  $('.empty-state h3').textContent = 'Your receipt needs a refresh.';
  $('.empty-state p').textContent = 'The intent or cart changed after review. Run the check again before you continue.';
  $('.empty-caption span:first-child').textContent = 'REVIEW OUT OF DATE';
  $('.scan-mark').textContent = '⌁';
  setFlow('intent');
}

async function api(url, body) {
  const response = await fetch(url, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const result = await response.json();
  if (!response.ok) throw Error(result.error || 'Request failed');
  return result;
}

function showReview(data) {
  const review = data.review;
  const overBudget = Boolean(review.budgetExceeded);
  const needsClarification = Boolean(review.needsClarification);
  const blocked = overBudget || needsClarification;
  $('#empty').hidden = true;
  $('.scan-mark').textContent = overBudget ? '!' : '✓';
  const output = $('#result');
  output.hidden = false;
  output.replaceChildren();

  const head = document.createElement('div');
  head.className = 'result-head';
  head.innerHTML = `<span class="riskdot ${review.risk}"></span><span class="risk-title">${overBudget ? 'Over your stated limit' : needsClarification ? 'Needs a clearer request' : review.risk === 'low' ? 'Looks aligned' : review.risk === 'high' ? 'High concern' : 'Worth a second look'}</span><span class="source"></span>`;
  head.querySelector('.source').textContent = review.source;
  output.append(head);

  const summary = document.createElement('p');
  summary.className = 'summary';
  summary.textContent = review.summary;
  output.append(summary);

  if (review.source.startsWith('AI model') && Number.isFinite(Number(review.confidence))) {
    const confidence = document.createElement('div');
    confidence.className = 'confidence';
    confidence.innerHTML = '<div class="confidence-caption"><span>MODEL ESTIMATE</span><span>NOT A GUARANTEE</span></div><div class="confidence-track"><i></i></div>';
    confidence.querySelector('i').style.setProperty('--confidence', `${Math.round(Math.max(0, Math.min(1, Number(review.confidence))) * 100)}%`);
    output.append(confidence);
  }

  for (const finding of review.findings || []) {
    const block = document.createElement('div');
    block.className = finding.severity === 'high' ? 'finding finding-critical' : 'finding';
    const title = document.createElement('b');
    title.textContent = finding.item;
    block.append(title, document.createTextNode(finding.reason));
    if (finding.evidence) {
      const evidence = document.createElement('div');
      evidence.className = 'evidence-pair';
      for (const [label, quote] of [['YOUR WORDS',finding.evidence.intent_quote],['CART EVIDENCE',finding.evidence.cart_quote]]) {
        const side = document.createElement('div');
        const caption = document.createElement('small');
        caption.textContent = label;
        const text = document.createElement('q');
        text.textContent = quote;
        side.append(caption,text);
        evidence.append(side);
      }
      block.append(evidence);
    }
    output.append(block);
  }

  if (review.repairSuggestion) {
    const repair = document.createElement('section');
    repair.className = 'repair-card';
    const kicker = document.createElement('div');
    kicker.className = 'repair-kicker';
    kicker.innerHTML = '<span class="repair-spark">✳</span> SMALLEST CART EDIT';
    const title = document.createElement('h4');
    title.textContent = review.repairSuggestion.kind === 'budget' ? 'Keep the core. Trim the overage.' : 'Remove the item you flagged.';
    const detail = document.createElement('p');
    detail.textContent = `Remove ${review.repairSuggestion.removeItems.join(', ')} · keep ${review.repairSuggestion.keepItems.join(', ')}.`;
    const currentTotal = Number(data.total);
    const revisedTotal = Number(review.repairSuggestion.newTotal);
    const limit = Number(review.budgetLimit) || null;
    const scale = Math.max(currentTotal, limit || 0);
    const marker = limit ? `${Math.min(100,limit/scale*100)}%` : '100%';
    const compare = document.createElement('div');
    compare.className = `repair-compare${limit ? ' has-limit' : ''}`;
    compare.style.setProperty('--limit-marker',marker);
    for (const [labelText,value,rowClass] of [['NOW',currentTotal,'current'],['AFTER YOUR EDIT',revisedTotal,'revised']]) {
      const row=document.createElement('div');
      row.className=`repair-bar-row ${rowClass}`;
      const label=document.createElement('small');
      label.textContent=labelText;
      const track=document.createElement('div');
      track.className='repair-bar-track';
      const fill=document.createElement('i');
      fill.style.setProperty('--bar-width',`${Math.min(100,value/scale*100)}%`);
      track.append(fill);
      const amount=document.createElement('strong');
      amount.textContent=`$${value.toFixed(2)}`;
      row.append(label,track,amount);
      compare.append(row);
    }
    if(limit){const cap=document.createElement('div');cap.className='repair-cap';cap.textContent=`YOUR $${limit.toFixed(2)} LIMIT`;compare.append(cap);}
    const math = document.createElement('div');
    math.className = 'repair-math';
    math.innerHTML = `<span>$${currentTotal.toFixed(2)}</span><i>→</i><strong>$${revisedTotal.toFixed(2)}</strong><small>$${Number(review.repairSuggestion.saved).toFixed(2)} freed</small>`;
    const action = document.createElement('button');
    action.className = 'repair-action';
    action.type = 'button';
    action.textContent = 'Apply this edit, then recheck';
    action.addEventListener('click', () => applyRepair(review.repairSuggestion.removeIndexes));
    const note = document.createElement('small');
    note.className = 'repair-note';
    note.textContent = 'Nothing changes until you choose. A fresh review is required; payment is never automatic.';
    repair.append(kicker,title,detail,compare,math,action,note);
    output.append(repair);
  }

  const list = document.createElement('div');
  list.className = 'checked-items';
  for (const item of data.items) {
    const line = document.createElement('div');
    const name = document.createElement('span');
    name.textContent = `${item.quantity} × ${item.name}`;
    const checked = document.createElement('strong');
    checked.textContent = 'in reviewed cart';
    line.append(name, checked);
    list.append(line);
  }
  const total = document.createElement('div');
  const label = document.createElement('strong');
  label.textContent = 'Reviewed total';
  const amount = document.createElement('strong');
  amount.textContent = `$${Number(data.total).toFixed(2)} USD`;
  total.append(label, amount);
  list.append(total);
  output.append(list);

  const confirm = document.createElement('label');
  confirm.className = `confirm${overBudget ? ' blocked' : ''}`;
  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  checkbox.id = 'confirm';
  checkbox.disabled = blocked;
  const explanation = document.createElement('span');
  if (overBudget) {
    const difference = (Number(data.total) - Number(review.budgetLimit)).toFixed(2);
    explanation.innerHTML = `<strong>Payment locked.</strong> Cart is $${difference} above your $${Number(review.budgetLimit).toFixed(2)} limit. Adjust items to continue.`;
    $('#payhint').textContent = 'The stated budget is a hard limit. Reduce the cart, then review again.';
    setFlow('review');
    $('#ticket-state').textContent = 'Budget limit exceeded';
    disablePayment('This cart exceeds the limit you stated.');
  } else if (needsClarification) {
    explanation.innerHTML = '<strong>Checkout locked.</strong> At least one AI concern could not be tied to exact request and cart text. Clarify your request and review again.';
    $('#payhint').textContent = 'Clarify the highlighted purchase intent before checkout.';
    setFlow('review');
    $('#ticket-state').textContent = 'Clarification needed';
    disablePayment('Clarify your request before checkout.');
    const clarify = document.createElement('button');
    clarify.className = 'clarify-action';
    clarify.type = 'button';
    clarify.textContent = 'Clarify my request';
    clarify.addEventListener('click', () => { $('#intent').focus(); $('#intent').select(); });
    output.append(clarify);
  } else {
    explanation.innerHTML = '<strong>I checked this cart and the total.</strong> Continue to PayPal to authorize this sandbox payment.';
    $('#payhint').textContent = 'Check the reviewed total and confirm below.';
  }
  confirm.append(checkbox, explanation);
  output.append(confirm);

  checkbox.onchange = () => {
    $('#payhint').textContent = checkbox.checked ? 'You control the final approval in PayPal.' : 'Confirm the reviewed cart to continue.';
    if (checkbox.checked) { setFlow('approve'); renderPaypal(); }
    else { setFlow('review'); disablePayment('Confirm the reviewed cart to continue.'); }
  };
}

function applyRepair(removeIndexes) {
  const rows = [...itemsEl.querySelectorAll('.item')];
  [...removeIndexes].sort((a,b)=>b-a).forEach((index)=>rows[index]?.remove());
  updateTotal();
  markStale();
  status('Your chosen cart edit is applied locally. Run a fresh review before you continue.');
  $('#review').scrollIntoView({behavior:'smooth',block:'center'});
}

async function review() {
  const button = $('#review');
  button.disabled = true;
  button.querySelector('span').textContent = 'Checking your cart…';
  status('');
  analysis = null;
  setFlow('review');
  $('#reviewcard').classList.add('is-checking');
  disablePayment('Running a fresh review…');
  try {
    const data = await api('/api/analyze', {intent:$('#intent').value,items:cart()});
    analysis = data;
    showReview(data);
    $('.empty-state h3').textContent = 'Nothing moves until it adds up.';
    $('.empty-state p').textContent = 'We compare your note with the cart, then show you what we found. No quiet edits. No surprise capture.';
    $('.empty-caption span:first-child').textContent = 'WAITING FOR INPUT';
    $('#reviewcard').scrollIntoView({behavior:'smooth',block:'nearest'});
  } catch (error) {
    status(error.message);
    $('#payhint').textContent = 'Review failed. No payment action is available.';
    setFlow('intent');
  } finally {
    $('#reviewcard').classList.remove('is-checking');
    button.disabled = false;
    button.querySelector('span').textContent = 'Check this purchase';
  }
}

async function renderPaypal() {
  if (!analysis || !$('#confirm').checked) return;
  if (!config.paypalClientId) { disablePayment('PayPal Sandbox credentials are required. See README.md.'); return; }
  if (!config.aiEnabled) { disablePayment('A live AI key is required for checkout; offline review is preview-only.'); return; }
  if (!window.paypal) { $('#payhint').textContent = 'PayPal SDK did not load. Check your connection and reload.'; return; }
  $('#paypal-button-container').replaceChildren();
  window.paypal.Buttons({
    createOrder: async () => {
      if (!analysis || !$('#confirm').checked) throw Error('Review and confirm the cart first.');
      const result = await api('/api/create-order', {analysisId:analysis.analysisId});
      return result.id;
    },
    onApprove: async (data) => {
      setFlow('verify');
      status('Verifying PayPal order state, merchant, items, and total…');
      try {
        const result = await api('/api/capture-order', {orderID:data.orderID});
        status(`PayPal response: order ${result.status || 'unknown'} · capture ${result.captureStatus || 'unknown'} · ${result.amount?.value || ''} ${result.amount?.currency_code || ''}. ${result.captureId ? 'Capture ID ' + result.captureId + '.' : ''}`);
        $('#payhint').textContent = result.paymentComplete ? 'PayPal confirmed a completed capture after the server verified the exact reviewed order.' : 'PayPal has not confirmed a completed capture. Do not treat this as paid.';
        if (result.paymentComplete) {
          flow.forEach((id) => { $(`#flow-${id}`).classList.remove('active'); $(`#flow-${id}`).classList.add('complete'); });
          $('#ticket-state').textContent = 'Order verified & captured';
        } else setFlow('review');
        analysis = null;
        $('#paypal-button-container').replaceChildren();
      } catch (error) {
        status(error.message);
        $('#payhint').textContent = 'Capture was blocked or could not be verified. No success is assumed.';
        setFlow('review');
      }
    },
    onCancel: () => { status('Checkout canceled in PayPal. No capture was requested.'); setFlow('approve'); },
    onError: (error) => { status('PayPal checkout error. No success is assumed. ' + (error?.message || '')); setFlow('review'); },
  }).render('#paypal-button-container').catch((error) => status('Could not render PayPal checkout: ' + error.message));
}

async function init() {
  [['USB-C charger','24.00',1],['Travel pouch','8.00',1]].forEach((item) => addItem(...item));
  $('#add').onclick = () => addItem();
  $('#review').onclick = review;
  $('#intent').value = 'A compact USB-C charger for travel, under $30';
  $('#intent').addEventListener('input', markStale);
  $('#how').onclick = () => $('#howdialog').showModal();
  $('#close').onclick = () => $('#howdialog').close();
  try {
    config = await fetch('/api/config').then((response) => response.json());
    if (config.paypalClientId) {
      const script = document.createElement('script');
      script.src = `https://www.paypal.com/sdk/js?client-id=${encodeURIComponent(config.paypalClientId)}&currency=USD&intent=capture`;
      script.onerror = () => status('Could not load the PayPal JavaScript SDK.');
      document.head.append(script);
    }
  } catch (error) { status('Server is not available. Start it with python3 server.py.'); }
}

init();
