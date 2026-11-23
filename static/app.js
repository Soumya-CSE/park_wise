document.addEventListener("DOMContentLoaded", function () {
  var p = document.getElementById("print-btn");
  if (p) p.addEventListener("click", function (e) { e.preventDefault(); window.print(); });
  document.querySelectorAll("input[name=plate]").forEach(function (el) {
    el.addEventListener("input", function () { el.value = el.value.toUpperCase(); });
  });
  document.querySelectorAll("form[data-confirm]").forEach(function (f) {
    f.addEventListener("submit", function (e) {
      if (!window.confirm(f.getAttribute("data-confirm"))) e.preventDefault();
    });
  });
});
