(function () {
    'use strict';
    var stages = document.getElementById('lactate-stages'), add = document.getElementById('add-lactate-stage'), mode = document.getElementById('lactate-mode');
    if (!stages || !add || !mode) return;
    var labels = { pace: 'Allure (min:sec/km)', speed: 'Vitesse (km/h)', power: 'Puissance (W)' };
    function update() {
        stages.querySelectorAll('[data-intensity-label]').forEach(function (el) { el.textContent = labels[mode.value]; });
        stages.querySelectorAll('[name=intensity]').forEach(function (el) { el.placeholder = mode.value === 'pace' ? '5:00' : mode.value === 'power' ? '200' : '12'; });
        add.disabled = stages.children.length >= 30;
    }
    if (stages.firstElementChild) add.hidden = false;
    add.addEventListener('click', function () {
        if (stages.children.length >= 30 || !stages.firstElementChild) return;
        var row = stages.firstElementChild.cloneNode(true);
        row.querySelector('legend').textContent = 'Palier ' + (stages.children.length + 1);
        row.querySelectorAll('input').forEach(function (el) { el.value = ''; });
        stages.appendChild(row); update(); row.querySelector('input').focus();
    });
    mode.addEventListener('change', update);
    document.querySelectorAll('[data-open-test]').forEach(function (el) { el.addEventListener('click', function () { document.getElementById('nouveau-test').open = true; }); });
    if (location.hash === '#nouveau-test') document.getElementById('nouveau-test').open = true;
    update();
})();
