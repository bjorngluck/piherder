/* About scheduled-jobs modal: one type at a time. */
(function () {
  function showSchedulerGroup() {
    var sel = document.getElementById("scheduler-job-type");
    if (!sel) return;
    var key = sel.value;
    var nodes = document.querySelectorAll("[data-scheduler-group]");
    for (var i = 0; i < nodes.length; i++) {
      var on = nodes[i].getAttribute("data-scheduler-group") === key;
      nodes[i].classList.toggle("hidden", !on);
    }
  }

  window.showSchedulerGroup = showSchedulerGroup;

  document.addEventListener("keydown", function (ev) {
    if (ev.key !== "Escape") return;
    var modal = document.getElementById("scheduler-jobs-modal");
    if (!modal || modal.classList.contains("hidden")) return;
    modal.classList.add("hidden");
    modal.classList.remove("flex");
  });
})();
