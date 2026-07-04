const DATA_URL = "./data/standings.json";
const AUTO_REFRESH_INTERVAL = 5 * 60 * 1000;

const groupFilters = document.querySelector("#groupFilters");
const scheduleFilters = document.querySelector("#scheduleFilters");
const miniGroupGrid = document.querySelector("#miniGroupGrid");
const groupsGrid = document.querySelector("#groupsGrid");
const matchesList = document.querySelector("#matchesList");
const teamSearch = document.querySelector("#teamSearch");
const emptyState = document.querySelector("#emptyState");
const scheduleEmpty = document.querySelector("#scheduleEmpty");
const bracketEmpty = document.querySelector("#bracketEmpty");
const emptyStateTitle = document.querySelector("#emptyStateTitle");
const emptyStateMessage = document.querySelector("#emptyStateMessage");
const menuButton = document.querySelector("#menuButton");
const refreshButton = document.querySelector("#refreshButton");
const syncStatus = document.querySelector("#syncStatus");
const nextMatchCard = document.querySelector("#nextMatchCard");
const bracketGrid = document.querySelector("#bracketGrid");

let dashboardData = null;
let groups = [];
let matches = [];
let activeGroup = "ALL";
let activeStage = "ALL";
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

function formatMatchTime(value) {
  if (!value) return "时间待定";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间待定";

  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  })
    .format(date)
    .replace(/\//g, ".")
    .replace(" ", " · ");
}

function isFinished(match) {
  return ["FINISHED", "AWARDED"].includes(match.status);
}

function scoreText(match) {
  const { score } = match;
  if (!score || !Number.isInteger(score.home) || !Number.isInteger(score.away)) {
    return "VS";
  }

  const base = `${score.home} - ${score.away}`;
  if (Number.isInteger(score.penaltiesHome) && Number.isInteger(score.penaltiesAway)) {
    return `${base} 点球 ${score.penaltiesHome}-${score.penaltiesAway}`;
  }
  return base;
}

const HTML_ENTITIES = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => HTML_ENTITIES[char]);
}

function safeCssColor(value, fallback = "#61727a") {
  const color = String(value ?? "").trim();
  return /^#[0-9a-f]{3,8}$/i.test(color) ? color : fallback;
}

function teamLabel(team = {}) {
  const colorA = safeCssColor(team.colorA);
  const colorB = safeCssColor(team.colorB, "#24323c");

  return `
    <span class="match-team" style="--nation-a:${colorA};--nation-b:${colorB}">
      <span class="flag-chip"><span>${escapeHtml(team.flag)}</span></span>
      <span>
        <strong>${escapeHtml(team.name)}</strong>
        <small>${escapeHtml(team.english)}</small>
      </span>
    </span>
  `;
}

function setSyncState(state, text) {
  refreshButton.classList.toggle("is-syncing", state === "syncing");
  refreshButton.classList.toggle("is-error", state === "error");
  refreshButton.disabled = state === "syncing";
  syncStatus.textContent = text;
}

function buildControls() {
  const filterIds = ["ALL", ...groups.map((group) => group.id)];
  const stageOptions = [
    ["ALL", "全部"],
    ...Array.from(
      new Map(
        matches.map((match) => [match.stage, match.stageLabel || match.stage]),
      ),
    ).sort((a, b) => {
      const orderA = matches.find((match) => match.stage === a[0])?.stageOrder ?? 99;
      const orderB = matches.find((match) => match.stage === b[0])?.stageOrder ?? 99;
      return orderA - orderB;
    }),
  ];

  groupFilters.innerHTML = filterIds
    .map(
      (id) => `
        <button class="filter-button ${id === activeGroup ? "is-active" : ""}" type="button" data-group="${escapeHtml(id)}">
          ${id === "ALL" ? "全部" : escapeHtml(id)}
        </button>
      `,
    )
    .join("");

  scheduleFilters.innerHTML = stageOptions
    .map(
      ([id, label]) => `
        <button class="filter-button ${id === activeStage ? "is-active" : ""}" type="button" data-stage="${escapeHtml(id)}">
          ${escapeHtml(label)}
        </button>
      `,
    )
    .join("");

  miniGroupGrid.innerHTML = groups
    .map(
      (group) => `
        <button class="mini-group-button ${group.id === activeGroup ? "is-active" : ""}" type="button" data-group="${escapeHtml(group.id)}" aria-label="查看 ${escapeHtml(group.id)} 组">
          ${escapeHtml(group.id)}
        </button>
      `,
    )
    .join("");
}

function groupCard(group, matchingTeams) {
  const leader = group.teams[0];
  const visibleTeamNames = new Set(matchingTeams.map((item) => item.name));
  const groupId = escapeHtml(group.id);
  const leaderColorA = safeCssColor(leader.colorA);
  const leaderColorB = safeCssColor(leader.colorB, "#50d5ff");

  return `
    <article
      class="group-card"
      id="group-${groupId}"
      style="--team-a:${leaderColorA};--team-b:${leaderColorB}"
      aria-label="${groupId} 组积分"
    >
      <header class="group-card__header">
        <div class="group-title">
          <span class="group-letter">${groupId}</span>
          <div>
            <strong>${groupId} 组</strong>
            <small>GROUP ${groupId}</small>
          </div>
        </div>
        <div class="leader-flags" title="当前前二">
          <span>${escapeHtml(group.teams[0].flag)}</span>
          <span>${escapeHtml(group.teams[1].flag)}</span>
        </div>
      </header>
      <div class="table-head" aria-hidden="true">
        <span>#</span><span>国家队</span><span>赛</span><span>净胜</span><span>积分</span>
      </div>
      ${group.teams
        .map(
          (item, index) => {
            const itemColorA = safeCssColor(item.colorA);
            const itemColorB = safeCssColor(item.colorB, "#50d5ff");
            return `
            <div
              class="team-row ${index < 2 ? "is-top-two" : ""}"
              style="--nation-a:${itemColorA};--nation-b:${itemColorB};${visibleTeamNames.has(item.name) ? "" : "opacity:.25"}"
            >
              <span class="rank">${escapeHtml(item.rank)}</span>
              <div class="team-name">
                <span class="flag-chip"><span>${escapeHtml(item.flag)}</span></span>
                <span class="team-copy">
                  <strong>${escapeHtml(item.name)}${index === 0 ? '<b class="qualified-tag">头名</b>' : ""}</strong>
                  <small>${escapeHtml(String(item.english ?? "").toUpperCase())}</small>
                </span>
              </div>
              <span class="cell">${escapeHtml(item.played)}</span>
              <span class="cell">${escapeHtml(signedNumber(item.goalDifference))}</span>
              <strong class="points">${escapeHtml(item.points)}</strong>
            </div>
          `;
          },
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

function matchCard(match, { compact = false } = {}) {
  const home = match.homeTeam || {};
  const away = match.awayTeam || {};
  const nextMatchId = dashboardData?.nextMatch?.id;
  const homeColor = safeCssColor(home.colorA);
  const awayColor = safeCssColor(away.colorB, "#50d5ff");

  return `
    <article
      class="match-card ${compact ? "match-card--compact" : ""} ${isFinished(match) ? "is-finished" : ""} ${match.id === nextMatchId ? "is-next" : ""}"
      style="--home-a:${homeColor};--away-b:${awayColor}"
    >
      <div class="match-meta">
        <span>${escapeHtml(match.stageLabel)}${match.group ? ` · ${escapeHtml(match.group)}组` : ""}</span>
        <strong>${escapeHtml(match.statusLabel)}</strong>
      </div>
      <div class="match-main">
        ${teamLabel(home)}
        <strong class="match-score">${escapeHtml(scoreText(match))}</strong>
        ${teamLabel(away)}
      </div>
      <div class="match-footer">
        <span>${formatMatchTime(match.utcDate)}</span>
        ${match.winner ? `<span>胜者：${escapeHtml(match.winner)}</span>` : ""}
      </div>
    </article>
  `;
}

function renderSchedule() {
  const visibleMatches = matches.filter((match) => activeStage === "ALL" || match.stage === activeStage);
  matchesList.innerHTML = visibleMatches.map((match) => matchCard(match)).join("");
  scheduleEmpty.hidden = visibleMatches.length > 0;
}

function renderNextMatch() {
  const nextMatch = dashboardData?.nextMatch || matches.find((match) => !isFinished(match));

  if (!nextMatch) {
    nextMatchCard.innerHTML = `
      <div class="next-match-empty">
        <span>✓</span>
        <div>
          <strong>暂无下一场比赛</strong>
          <small>如果全赛程已完结，这里会保持完成状态；如果淘汰赛尚未公布，自动更新后会出现下一场。</small>
        </div>
      </div>
    `;
    return;
  }

  nextMatchCard.innerHTML = `
    <div class="next-match-card__time">
      <span>${escapeHtml(nextMatch.stageLabel)}</span>
      <strong>${formatMatchTime(nextMatch.utcDate)}</strong>
      <small>${escapeHtml(nextMatch.statusLabel)}</small>
    </div>
    <div class="next-match-card__teams">
      ${teamLabel(nextMatch.homeTeam)}
      <strong>${escapeHtml(scoreText(nextMatch))}</strong>
      ${teamLabel(nextMatch.awayTeam)}
    </div>
  `;
}

function renderBracket() {
  const rounds = dashboardData?.knockout?.rounds || [];

  bracketGrid.innerHTML = rounds
    .map(
      (round) => `
        <section class="bracket-round">
          <h3>${escapeHtml(round.label)}</h3>
          <div class="bracket-round__matches">
            ${round.matches.map((match) => matchCard(match, { compact: true })).join("")}
          </div>
        </section>
      `,
    )
    .join("");
  bracketEmpty.hidden = rounds.length > 0;
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
  document.querySelector("#progressCount").textContent = `${summary.playedMatches} / ${summary.totalMatches || "--"}`;
  document.querySelector("#progressBar").style.width = `${progress}%`;
  document.querySelector("#progressLabel").textContent = `全赛程已完成 ${progress.toFixed(1)}%`;
  document.querySelector("#progressTrack").setAttribute("aria-label", `全赛程完成进度 ${progress.toFixed(1)}%`);
  document.querySelector("#topbarDate").textContent = formatBeijingTime(updatedAt);
  document.querySelector("#footerUpdatedAt").textContent = `数据截至 ${formatBeijingTime(updatedAt)}`;
  document.querySelector("#footerSource").textContent = `数据源：${source.label}`;
}

function renderStatus() {
  const leaders = groups.map((group) => ({ group: group.id, ...group.teams[0] }));
  const completedGroups = groups.filter((group) => group.teams.every((item) => item.played >= 3));
  const mostAdvanced = Math.max(...groups.flatMap((group) => group.teams.map((item) => item.played)), 0);
  const nextMatch = dashboardData?.nextMatch;

  document.querySelector("#statusHeadline").textContent = nextMatch
    ? `下一场：${nextMatch.homeTeam.name} 对 ${nextMatch.awayTeam.name}`
    : completedGroups.length === groups.length
      ? "小组赛已完成，等待或展示淘汰赛路径"
      : `最新积分已同步，当前最高完成 ${mostAdvanced} 轮`;
  document.querySelector("#statusSummary").textContent =
    "全赛程、淘汰赛比分与小组积分随数据源自动刷新；最终排名以赛事官方结果为准。";
  document.querySelector("#leaderFlags").innerHTML = leaders
    .slice(0, 5)
    .map((item) => `<span title="${escapeHtml(item.group)}组 · ${escapeHtml(item.name)}">${escapeHtml(item.flag)}</span>`)
    .join("");
}

function applyData(data) {
  if (!data || !Array.isArray(data.groups) || data.groups.length !== 12) {
    throw new Error("积分数据格式不完整");
  }

  dashboardData = data;
  groups = data.groups;
  matches = Array.isArray(data.matches) ? data.matches : [];

  if (activeGroup !== "ALL" && !groups.some((group) => group.id === activeGroup)) {
    activeGroup = "ALL";
  }
  if (activeStage !== "ALL" && !matches.some((match) => match.stage === activeStage)) {
    activeStage = "ALL";
  }

  buildControls();
  renderOverview(data);
  renderNextMatch();
  renderSchedule();
  renderBracket();
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

scheduleFilters.addEventListener("click", (event) => {
  const button = event.target.closest("[data-stage]");
  if (!button) return;

  activeStage = button.dataset.stage;
  document.querySelectorAll("[data-stage]").forEach((item) => {
    item.classList.toggle("is-active", item.dataset.stage === activeStage);
  });
  renderSchedule();
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
