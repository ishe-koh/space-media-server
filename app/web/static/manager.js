(() => {
  const upload = document.getElementById('upload-form');
  if (upload) {
    const update = () => {
      const purpose = upload.querySelector('[name=purpose]:checked').value;
      document.getElementById('upload-day').hidden = purpose !== 'weekday';
      document.getElementById('upload-directory').value = purpose === 'weekday' ? document.getElementById('upload-weekday').value : purpose;
      upload.querySelector('[name=available_until]').required = purpose === 'is_limited';
      document.getElementById('date-note').textContent = purpose === 'is_limited' ? '期間限定の素材は終了日時が必須です。日時は日本時間です。' : '使用期間を付けたい場合だけ指定します。';
    };
    upload.querySelectorAll('[name=purpose]').forEach(input => input.addEventListener('change', update));
    document.getElementById('upload-weekday').addEventListener('change', update);
    document.getElementById('upload-file').addEventListener('change', event => {
      document.getElementById('file-name').textContent = event.target.files[0]?.name || '1ファイルずつ追加できます';
    });
    update();
  }
  document.getElementById('asset-search')?.addEventListener('input', event => {
    const term = event.target.value.trim().toLowerCase();
    document.querySelectorAll('.asset').forEach(asset => asset.hidden = !asset.dataset.search.includes(term));
  });
  const schedule = document.getElementById('schedule-form');
  if (schedule) {
    const updateOrder = () => {
      const ordered = [...schedule.querySelectorAll('.order-row')].filter(row => row.querySelector('.order-check').checked).map(row => row.dataset.source);
      document.getElementById('ordered-items').value = JSON.stringify(ordered);
    };
    const updateMode = () => document.getElementById('custom-order').hidden = schedule.querySelector('[name=selection_mode]:checked').value !== 'custom';
    schedule.querySelectorAll('[name=selection_mode]').forEach(input => input.addEventListener('change', updateMode));
    schedule.querySelectorAll('.order-check').forEach(input => input.addEventListener('change', updateOrder));
    schedule.querySelectorAll('.move-up,.move-down').forEach(button => button.addEventListener('click', () => {
      const row = button.closest('.order-row');
      if (button.classList.contains('move-up') && row.previousElementSibling?.classList.contains('order-row')) row.previousElementSibling.before(row);
      if (button.classList.contains('move-down') && row.nextElementSibling?.classList.contains('order-row')) row.nextElementSibling.after(row);
      updateOrder();
    }));
    document.getElementById('all-day').addEventListener('change', event => document.getElementById('time-range').hidden = event.target.checked);
    schedule.addEventListener('submit', event => {
      updateOrder();
      if (schedule.querySelector('[name=selection_mode]:checked').value === 'custom' && JSON.parse(document.getElementById('ordered-items').value).length === 0) {
        event.preventDefault();
        alert('再生する素材を1つ以上選択してください。');
      }
    });
  }
  document.getElementById('known-target')?.addEventListener('change', event => {
    if (event.target.value) document.querySelector('[name=target_manual]').value = event.target.value;
  });
  document.querySelectorAll('form').forEach(form => form.addEventListener('submit', event => {
    if (event.defaultPrevented) return;
    const button = event.submitter;
    // Keep the chosen submit action intact; only prevent a second click.
    setTimeout(() => { if (button) { button.disabled = true; button.dataset.originalLabel = button.textContent; button.textContent = '処理しています…'; } }, 0);
  }));
})();
