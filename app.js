const DATA_URL = "./data/standings.json";
const AUTO_REFRESH_INTERVAL = 5 * 60 * 1000;

const groupFilters = document.querySelector("#groupFilters");
const miniGroupGrid = document.querySelector("#miniGroupGrid");
const groupsGrid = document.querySelector("#groupsGrid");
const teamSearch = document.querySelector("#teamSearch");
const emptyState = document.querySelector("#emptyState");
const emptyStateTitle = document.querySelector("#emptyStateTitle");
const emptyStateMessage = document.querySelector("#emptyStateMessage");
const menuButton = document.querySelector("#menuButton");
const refreshButton = document.querySelector("#refreshButton");
const syncStatus = document.querySelector("#syncStatus");

let dashboardData = null;
let groups = [];
let activeGroup = "ALL";
let searchTerm = "";
let isLoading = false;

function signedNumber(number) {
  if (number > 0) return `+${number}`;
  return String(number);
}

function formatBeijingTime(value, prefix = "北京时间") {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "更新时间未知";

  const formatted = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);

  return `${prefix} ${formatted.replace("/", ".").replace(" ", " · ")}`;
}

function setSyncState(state, text) {
  refreshButton.classList.toggle("is-syncing", state === "syncing");
  refreshButton.classList.toggle("is-error", state === "error");
  refreshButton.disabled = state === "syncing";
  syncStatus.textContent = text;
}

function buildControls() {
  const filterIds = ["ALL", ...groups.map((group) => group.id)];

  groupFilters.innerHTML = filterIds
    .map(
      (id) => `
        <button class="filter-button ${id === activeGroup ? "is-active" : ""}" type="button" data-group="${id}">
          ${id === "ALL" ? "全部" : id}
        </button>
      `,
    )
    .join("");

  miniGroupGrid.innerHTML = groups
    .map(
      (group) => `
        <button class="mini-group-button ${group.id === activeGroup ? "is-active" : ""}" type="button" data-group="${group.id}" aria-label="查看 ${group.id} 组">
          ${group.id}
        </button>
      `,
    )
    .join("");
}

function groupCard(group, matchingTeams) {
  const leader = group.teams[0];
  const visibleTeamNames = new Set(matchingTeams.map((item) => item.name));

  return `
    <article
      class="group-card"
      id="group-${group.id}"
      style="--team-a:${leader.colorA};--team-b:${leader.colorB}"
      aria-label="${group.id} 组积分"
    >
      <header class="group-card__header">
        <div class="group-title">
          <span class="group-letter">${group.id}</span>
          <div>
            <strong>${group.id} 组</strong>
            <small>GROUP ${group.id}</small>
          </div>
        </div>
        <div class="leader-flags" title="当前前二">
          <span>${group.teams[0].flag}</span>
          <span>${group.teams[1].flag}</span>
        </div>
      </header>
      <div class="table-head" aria-hidden="true">
        <span>#</span><span>国家队</span><span>赛</span><span>净胜</span><span>积分</span>
      </div>
      ${group.teams
        .map(
          (item, index) => `
            <div
              class="team-row ${index < 2 ? "is-top-two" : ""}"
              style="--nation-a:${item.colorA};--nation-b:${item.colorB};${visibleTeamNames.has(item.name) ? "" : "opacity:.25"}"
            >
              <span class="rank">${item.rank}</span>
              <div class="team-name">
                <span class="flag-chip"><span>${item.flag}</span></span>
                <span class="team-copy">
                  <strong>${item.name}${index === 0 ? '<b class="qualified-tag">头名</b>' : ""}</strong>
                  <small>${item.english.toUpperCase()}</small>
                </span>
              </div>
              <span class="cell">${item.played}</span>
              <span class="cell">${signedNumber(item.goalDifference)}</span>
              <strong class="points">${item.points}</strong>
            </div>
          `,
        )
        .join("")}
    </article>
  `;
}

function renderGroups() {
  const normalizedSearch = searchTerm.trim().toLocaleLowerCase("zh-CN");
  const visibleGroups = groups
    .filter((group) => activeGroup === "ALL" || group.id === activeGroup)
    .map((group) => {
      const matchingTeams = group.teams.filter(
        (item) =>
          !normalizedSearch ||
          item.name.toLocaleLowerCase("zh-CN").includes(normalizedSearch) ||
          item.english.toLocaleLowerCase().includes(normalizedSearch),
      );
      return { group, matchingTeams };
    })
    .filter(({ matchingTeams }) => matchingTeams.length > 0);

  groupsGrid.innerHTML = visibleGroups.map(({ group, matchingTeams }) => groupCard(group, matchingTeams)).join("");
  emptyState.hidden = visibleGroups.length > 0;

  if (!visibleGroups.length) {
    emptyStateTitle.textContent = dashboardData ? "未找到匹配的国家队" : "暂时无法载入积分";
    emptyStateMessage.textContent = dashboardData
      ? "请尝试其他关键词或切换小组"
      : "请通过本地服务器打开页面，或稍后重试";
  }
}

function renderOverview(data) {
  const { summary, updatedAt, source } = data;
  const progress = summary.totalMatches
    ? Math.min(100, (summary.playedMatches / summary.totalMatches) * 100)
    : 0;

  document.querySelector("#teamCount").textContent = summary.teams;
  document.querySelector("#matchCount").textContent = summary.playedMatches;
  document.querySelector("#goalCount").textContent = summary.goals;
  document.querySelector("#goalAverage").textContent = summary.averageGoals.toFixed(2);
  document.querySelector("#progressCount").textContent = `${summary.playedMatches} / ${summary.totalMatches}`;
  document.querySelector("#progressBar").style.width = `${progress}%`;
  document.querySelector("#progressLabel").textContent = `已完成 ${progress.toFixed(1)}%`;
  document.querySelector("#progressTrack").setAttribute("aria-label", `小组赛完成进度 ${progress.toFixed(1)}%`);
  document.querySelector("#topbarDate").textContent = formatBeijingTime(updatedAt);
  document.querySelector("#footerUpdatedAt").textContent = `数据截至 ${formatBeijingTime(updatedAt)}`;
  document.querySelector("#footerSource").textContent = `数据源：${source.label}`;
}

function renderStatus() {
  const leaders = groups.map((group) => ({ group: group.id, ...group.teams[0] }));
  const completedGroups = groups.filter((group) => group.teams.every((item) => item.played >= 3));
  const mostAdvanced = Math.max(...groups.flatMap((group) => group.teams.map((item) => item.played)), 0);

  document.querySelector("#statusHeadline").textContent = completedGroups.length
    ? `${completedGroups.map((group) => `${group.id}组`).join("、")} 已完成全部小组赛`
    : `最新积分已同步，当前最高完成 ${mostAdvanced} 轮`;
  document.querySelector("#statusSummary").textContent =
    "排名按积分、总净胜球、总进球及相互战绩自动计算；最终排名以赛事官方结果为准。";
  document.querySelector("#leaderFlags").innerHTML = leaders
    .slice(0, 5)
    .map((item) => `<span title="${item.group}组 · ${item.name}">${item.flag}</span>`)
    .join("");
}

function applyData(data) {
  if (!data || !Array.isArray(data.groups) || data.groups.length !== 12) {
    throw new Error("积分数据格式不完整");
  }

  dashboardData = data;
  groups = data.groups;

  if (activeGroup !== "ALL" && !groups.some((group) => group.id === activeGroup)) {
    activeGroup = "ALL";
  }

  buildControls();
  renderOverview(data);
  renderGroups();
  renderStatus();
}

async function loadStandings({ manual = false } = {}) {
  if (isLoading) return;
  isLoading = true;
  setSyncState("syncing", manual ? "正在刷新" : "正在同步");

  try {
    const response = await fetch(`${DATA_URL}?v=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const data = await response.json();
    applyData(data);
    setSyncState("ready", "数据已更新");
  } catch (error) {
    console.error("Failed to load standings:", error);
    setSyncState("error", dashboardData ? "使用缓存数据" : "同步失败");
    renderGroups();
  } finally {
    isLoading = false;
  }
}

function setActiveGroup(id) {
  activeGroup = id;

  document.querySelectorAll("[data-group]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.group === id);
  });

  renderGroups();

  if (window.innerWidth <= 900) {
    document.body.classList.remove("menu-open");
  }
}

groupFilters.addEventListener("click", (event) => {
  const button = event.target.closest("[data-group]");
  if (button) setActiveGroup(button.dataset.group);
});

miniGroupGrid.addEventListener("click", (event) => {
  const button = event.target.closest("[data-group]");
  if (!button) return;

  setActiveGroup(button.dataset.group);
  document.querySelector("#groups").scrollIntoView({ behavior: "smooth" });
});

teamSearch.addEventListener("input", (event) => {
  searchTerm = event.target.value;
  renderGroups();
});

refreshButton.addEventListener("click", () => loadStandings({ manual: true }));

menuButton.addEventListener("click", () => {
  document.body.classList.toggle("menu-open");
});

document.addEventListener("click", (event) => {
  if (
    window.innerWidth <= 900 &&
    document.body.classList.contains("menu-open") &&
    !event.target.closest(".sidebar") &&
    !event.target.closest("#menuButton")
  ) {
    document.body.classList.remove("menu-open");
  }
});

renderGroups();
loadStandings();
window.setInterval(loadStandings, AUTO_REFRESH_INTERVAL);
