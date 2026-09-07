(function () {
  "use strict";
  const hamburgerBtn = document.getElementById("hamburgerBtn");
  const sidebar = document.getElementById("sidebar");
  const backdrop = document.getElementById("backdrop");
  if (!sidebar || !hamburgerBtn || !backdrop) return;

  hamburgerBtn.addEventListener("click", function () {
    sidebar.classList.toggle("open");
    backdrop.style.display = sidebar.classList.contains("open") ? "block" : "none";
  });

  backdrop.addEventListener("click", function () {
    sidebar.classList.remove("open");
    backdrop.style.display = "none";
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && sidebar.classList.contains("open")) {
      sidebar.classList.remove("open");
      backdrop.style.display = "none";
    }
  });

  document.querySelectorAll(".sidebar-nav a").forEach((link) => {
    link.addEventListener("click", function () {
      sidebar.classList.remove("open");
      backdrop.style.display = "none";
    });
  });

  const currentPath = window.location.pathname;
  document.querySelectorAll(".sidebar-nav a").forEach((link) => {
    if (link.getAttribute("href") === currentPath) {
      link.classList.add("active");
    }
  });
})();
