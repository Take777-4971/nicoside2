let currentProvider = null;
let currentVideoId = null;
let hasActiveSearch = false;
let lastAppliedPageSize = null;
let currentSort = null;

// リピートモード: "off" | "single"(1曲) | "list"(表示中のリスト/検索結果)
let repeatMode = "off";
// リストリピート対象（現在一覧に表示されているページの動画ID列）
let currentListItems = [];
let currentListIndex = -1;

// YouTube IFrame Player API（リストリピートで動画終了を検知するために使用）
let ytApiReady = false;
let ytPlayer = null;

const ITEM_SLOT_HEIGHT = 63; // 45(サムネ高さ) + 12(padding) + 6(gap) の目安
const MIN_PAGE_SIZE = 1;
const MAX_PAGE_SIZE = 20;
const MIN_PLAYER_HEIGHT = 120;

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str == null ? "" : String(str);
  return div.innerHTML;
}

function waitForPywebview(callback) {
  if (window.pywebview && window.pywebview.api) {
    callback();
  } else {
    window.addEventListener("pywebviewready", callback);
  }
}

// ----------------------------------------------------------------------
// YouTube IFrame Player API のロード
// （リストリピート機能で「動画が終わったら次を再生する」を検知するために使う。
//  単曲リピート・通常再生では使わず、通常のiframe直読み込みのまま）
// ----------------------------------------------------------------------
function loadYouTubeIframeAPI() {
  if (window.YT && window.YT.Player) {
    ytApiReady = true;
    return;
  }
  if (document.getElementById("yt-iframe-api-script")) return;
  const tag = document.createElement("script");
  tag.id = "yt-iframe-api-script";
  tag.src = "https://www.youtube.com/iframe_api";
  document.head.appendChild(tag);
  window.onYouTubeIframeAPIReady = function () {
    ytApiReady = true;
  };
}

// プレイヤーの高さは「実際に描画されている幅」から16:9になるよう計算する。
function applyPlayerHeight() {
  const player = document.getElementById("player-container");
  const width = player.clientWidth || player.offsetWidth;
  let height = Math.round((width * 9) / 16);
  if (!isFinite(height) || height < MIN_PLAYER_HEIGHT) height = MIN_PLAYER_HEIGHT;
  player.style.height = height + "px";
  player.style.minHeight = height + "px";
}

function calculatePageSize() {
  const list = document.getElementById("results-list");
  const availableHeight = list.clientHeight;
  let count = Math.floor((availableHeight + 6) / ITEM_SLOT_HEIGHT);
  if (!isFinite(count) || count < MIN_PAGE_SIZE) count = MIN_PAGE_SIZE;
  if (count > MAX_PAGE_SIZE) count = MAX_PAGE_SIZE;
  return count;
}

async function loadProviders() {
  const select = document.getElementById("provider-select");
  const names = await window.pywebview.api.get_provider_names();
  select.innerHTML = "";
  names.forEach((name) => {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    select.appendChild(opt);
  });
  // 先頭（YouTube）を初期選択
  currentProvider = names[0] || null;
  select.value = currentProvider;
}

// ----------------------------------------------------------------------
// YouTubeアカウント連携（OAuth）
// ----------------------------------------------------------------------
async function refreshAccountStatus() {
  const loggedIn = await window.pywebview.api.is_youtube_logged_in();
  updateAccountUI(loggedIn, false);
  return loggedIn;
}

function updateAccountUI(loggedIn, busy) {
  const btn = document.getElementById("account-btn");
  const label = document.getElementById("account-state");
  if (busy) {
    label.textContent = "...";
    return;
  }
  btn.classList.toggle("logged-in", !!loggedIn);
  label.textContent = loggedIn ? "済" : "未";
  btn.title = loggedIn
    ? "YouTubeアカウント: ログイン済み（クリックでログイン/ログアウト）"
    : "YouTubeアカウント: 未ログイン（クリックでログイン/ログアウト）";
}

function toggleAccountMenu(show) {
  const menu = document.getElementById("account-menu");
  if (show === undefined) {
    menu.classList.toggle("hidden");
  } else {
    menu.classList.toggle("hidden", !show);
  }
}

async function handleAccountMenuAction(action) {
  toggleAccountMenu(false);
  if (action === "login") {
    updateAccountUI(false, true);
    // ここでブラウザが開き、ユーザーがログインを完了するまで待つ
    const result = await window.pywebview.api.login_youtube_oauth();
    const loggedIn = await refreshAccountStatus();
    if (!result.success) {
      window.alert("ログインに失敗しました: " + (result.error || "不明なエラー"));
    }
    // ログイン状態が変わったので、再生リストを読み直す
    if (currentProvider === "YouTube") {
      await loadYoutubePlaylists();
    }
  } else if (action === "logout") {
    await window.pywebview.api.logout_youtube();
    await refreshAccountStatus();
    if (currentProvider === "YouTube") {
      await loadYoutubePlaylists();
    }
  }
}

// ----------------------------------------------------------------------
// ニコニコ動画アカウント連携（非公式ログイン。メモリ上のみ保持され、
// アプリ終了で自動的にログアウトされる）
// ----------------------------------------------------------------------
async function refreshNicoAccountStatus() {
  const loggedIn = await window.pywebview.api.is_niconico_logged_in();
  updateNicoAccountUI(loggedIn, false);
  return loggedIn;
}

function updateNicoAccountUI(loggedIn, busy) {
  const btn = document.getElementById("nico-account-btn");
  const label = document.getElementById("nico-account-state");
  if (busy) {
    label.textContent = "...";
    return;
  }
  btn.classList.toggle("logged-in", !!loggedIn);
  label.textContent = loggedIn ? "済" : "未";
  btn.title = loggedIn
    ? "ニコニコ動画アカウント: ログイン済み（アプリ終了で自動ログアウトされます）"
    : "ニコニコ動画アカウント: 未ログイン（クリックでログイン/ログアウト）";
}

function toggleNicoAccountMenu(show) {
  const menu = document.getElementById("nico-account-menu");
  if (show === undefined) {
    menu.classList.toggle("hidden");
  } else {
    menu.classList.toggle("hidden", !show);
  }
}

async function handleNicoAccountMenuAction(action) {
  toggleNicoAccountMenu(false);
  if (action === "login") {
    updateNicoAccountUI(false, true);
    // 別ウィンドウが開き、ユーザーがログインを完了するまで待つ
    const result = await window.pywebview.api.open_niconico_login();
    await refreshNicoAccountStatus();
    if (!result.success) {
      window.alert("ログインに失敗しました: " + (result.error || "不明なエラー"));
    }
    if (currentProvider === "ニコニコ動画") {
      await loadNiconicoMylists();
    }
  } else if (action === "logout") {
    await window.pywebview.api.logout_niconico();
    await refreshNicoAccountStatus();
    if (currentProvider === "ニコニコ動画") {
      await loadNiconicoMylists();
    }
  }
}

// ----------------------------------------------------------------------
// ソースごとにリスト関連のUI・処理を切り替える
// （YouTubeは再生リスト、ニコニコ動画はマイリストに対応）
// ----------------------------------------------------------------------
async function onProviderChanged() {
  currentProvider = document.getElementById("provider-select").value;
  const playlistRow = document.getElementById("playlist-row");
  const playlistLabel = document.getElementById("playlist-row-label");
  const playlistSelect = document.getElementById("playlist-select");
  const mylistAddBtn = document.getElementById("mylist-add-btn");
  const mylistRemoveBtn = document.getElementById("mylist-remove-btn");
  const commentBtnWrap = document.getElementById("comment-btn-wrap");
  const nicoAccountBtnWrap = document.getElementById("nico-account-btn-wrap");
  const accountBtnWrap = document.getElementById("account-btn-wrap");

  playlistRow.classList.remove("hidden");
  playlistSelect.innerHTML = '<option value="">-- 検索結果を表示 --</option>';
  await loadSortOptions();

  if (currentProvider === "YouTube") {
    playlistLabel.textContent = "再生リスト:";
    mylistAddBtn.classList.add("hidden");
    mylistRemoveBtn.classList.add("hidden");
    commentBtnWrap.classList.add("hidden");
    nicoAccountBtnWrap.classList.add("hidden");
    accountBtnWrap.classList.remove("hidden");
    await loadYoutubePlaylists();
  } else if (currentProvider === "ニコニコ動画") {
    playlistLabel.textContent = "マイリスト:";
    mylistAddBtn.classList.remove("hidden");
    mylistRemoveBtn.classList.remove("hidden");
    commentBtnWrap.classList.remove("hidden");
    nicoAccountBtnWrap.classList.remove("hidden");
    // YouTube用のログインボタンは、ニコニコ動画選択中は無関係なので隠す
    accountBtnWrap.classList.add("hidden");
    await refreshNicoAccountStatus();
    await loadNiconicoMylists();
  } else {
    playlistRow.classList.add("hidden");
    mylistAddBtn.classList.add("hidden");
    mylistRemoveBtn.classList.add("hidden");
    commentBtnWrap.classList.add("hidden");
    nicoAccountBtnWrap.classList.add("hidden");
    accountBtnWrap.classList.remove("hidden");
  }
}

// ----------------------------------------------------------------------
// 並び替え（ソースによって選べる項目が異なるため、都度読み込み直す）
// ----------------------------------------------------------------------
async function loadSortOptions() {
  const row = document.getElementById("sort-row");
  const select = document.getElementById("sort-select");
  const options = await window.pywebview.api.get_sort_options(currentProvider);
  select.innerHTML = "";
  if (!options || options.length === 0) {
    row.classList.add("hidden");
    currentSort = null;
    return;
  }
  row.classList.remove("hidden");
  options.forEach((opt) => {
    const el = document.createElement("option");
    el.value = opt.value;
    el.textContent = opt.label;
    select.appendChild(el);
  });
  // ソースを切り替えるたびに、そのソースの既定（先頭の選択肢）に戻す
  currentSort = options[0].value;
  select.value = currentSort;
}

async function onSortChanged() {
  currentSort = document.getElementById("sort-select").value;
  // 並び替えを変更した時点で、再生リスト表示中でなければ再検索する
  const playlistSelect = document.getElementById("playlist-select");
  if (hasActiveSearch && (!playlistSelect || !playlistSelect.value)) {
    await performSearch();
  }
}

async function loadYoutubePlaylists() {
  const select = document.getElementById("playlist-select");
  select.innerHTML = '<option value="">-- 検索結果を表示 --</option>';
  const result = await window.pywebview.api.get_youtube_playlists();
  if (result.error) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = `(${result.error})`;
    opt.disabled = true;
    select.appendChild(opt);
    return;
  }
  (result.playlists || []).forEach((pl) => {
    const opt = document.createElement("option");
    opt.value = pl.id;
    opt.textContent = pl.title;
    select.appendChild(opt);
  });
}

async function loadNiconicoMylists() {
  const select = document.getElementById("playlist-select");
  const previousValue = select.value;
  select.innerHTML = '<option value="">-- 検索結果を表示 --</option>';

  // 2026-08-15: 正しいエンドポイント（v1/users/me/mylists）が判明した
  // ため、自分のマイリスト一覧の自動取得を復活させる。ログインしていない
  // 場合はエラーになるが、その旨のアラートは出さず単に空のまま扱う
  // （ログインは任意機能のため）
  const ownResult = await window.pywebview.api.get_niconico_own_mylists();
  if ((ownResult.mylists || []).length > 0) {
    const ownGroup = document.createElement("optgroup");
    ownGroup.label = "自分のマイリスト";
    ownResult.mylists.forEach((ml) => {
      const opt = document.createElement("option");
      opt.value = ml.id;
      opt.textContent = ml.title;
      ownGroup.appendChild(opt);
    });
    select.appendChild(ownGroup);
  }

  const result = await window.pywebview.api.get_niconico_mylists();
  if ((result.mylists || []).length > 0) {
    const registeredGroup = document.createElement("optgroup");
    registeredGroup.label = "登録済み（公開マイリスト）";
    result.mylists.forEach((ml) => {
      const opt = document.createElement("option");
      opt.value = ml.id;
      opt.textContent = ml.title;
      registeredGroup.appendChild(opt);
    });
    select.appendChild(registeredGroup);
  }

  // 直前の選択を維持できる場合は維持する（追加・削除直後の再読み込み用）
  if (previousValue && [...select.options].some((o) => o.value === previousValue)) {
    select.value = previousValue;
  }
}

async function addNiconicoMylist() {
  const input = window.prompt(
    "登録したいマイリストのURLまたはIDを入力してください\n" +
      "（マイリストは「公開」設定になっている必要があります）\n" +
      "例: https://www.nicovideo.jp/mylist/12345678 または 12345678"
  );
  if (!input) return;
  const result = await window.pywebview.api.register_niconico_mylist(input.trim());
  if (result.error) {
    window.alert(result.error);
  }
  await loadNiconicoMylists();
}

async function removeNiconicoMylist() {
  const select = document.getElementById("playlist-select");
  const mylistId = select.value;
  if (!mylistId) {
    window.alert("登録解除するマイリストをドロップダウンから選択してください");
    return;
  }
  const title = select.options[select.selectedIndex]?.textContent || mylistId;
  if (!window.confirm(`「${title}」の登録を解除しますか？（マイリスト自体は削除されません）`)) return;
  await window.pywebview.api.remove_niconico_mylist(mylistId);
  select.value = "";
  await loadNiconicoMylists();
  applySearchResult({ items: [], page: 1, page_size: lastAppliedPageSize, has_prev: false, has_next: false });
}

async function onPlaylistSelected() {
  const select = document.getElementById("playlist-select");
  const playlistId = select.value;
  if (!playlistId) return; // 「検索結果を表示」に戻した場合は何もしない
  const pageSize = calculatePageSize();
  const result = await window.pywebview.api.open_playlist(currentProvider, playlistId, pageSize);
  hasActiveSearch = true;
  applySearchResult(result);
}

async function performSearch() {
  const input = document.getElementById("search-input");
  const query = input.value.trim();
  if (!query) return;
  currentProvider = document.getElementById("provider-select").value;
  // キーワード検索を行ったら、再生リストの選択状態は解除する
  const playlistSelect = document.getElementById("playlist-select");
  if (playlistSelect) playlistSelect.value = "";
  const pageSize = calculatePageSize();
  const result = await window.pywebview.api.search(currentProvider, query, pageSize, currentSort);
  hasActiveSearch = true;
  applySearchResult(result);
}

function applySearchResult(result) {
  lastAppliedPageSize = result.page_size || lastAppliedPageSize;
  renderResults(result.items || []);
  updatePaginationUI(result);
}

function renderResults(results) {
  const list = document.getElementById("results-list");
  list.innerHTML = "";
  // リストリピート用: このページに表示されている再生可能な動画IDの並び
  currentListItems = results.filter((item) => !!item.id).map((item) => item.id);
  currentListIndex = currentVideoId ? currentListItems.indexOf(currentVideoId) : -1;

  results.forEach((item) => {
    const playable = !!item.id;
    const el = document.createElement("div");
    el.className = "result-item" + (playable ? "" : " disabled");
    const thumbHtml = item.thumbnail_url
      ? `<img class="thumb" src="${escapeHtml(item.thumbnail_url)}" />`
      : `<div class="thumb"></div>`;
    el.innerHTML = `
      ${thumbHtml}
      <div class="meta">
        <div class="title">${escapeHtml(item.title)}</div>
        <div class="source">${escapeHtml(item.source_name)}</div>
      </div>
    `;
    if (playable) {
      el.addEventListener("click", () => playVideo(item.id));
    }
    list.appendChild(el);
  });
}

function updatePaginationUI(result) {
  const pageInfo = document.getElementById("page-info");
  let totalText = "";
  if (result.total_count != null) {
    const capped = result.max_results != null && result.total_count > result.max_results;
    totalText = capped
      ? ` / 全${result.total_count}件中 上位${result.max_results}件まで`
      : ` / 全${result.total_count}件`;
  }
  pageInfo.textContent = `${result.page}ページ目${totalText}`;

  document.getElementById("prev-btn").disabled = !result.has_prev;
  document.getElementById("next-btn").disabled = !result.has_next;
  document.getElementById("first-btn").disabled = result.page <= 1;
  document.getElementById("last-btn").style.display = result.supports_last ? "inline-block" : "none";

  if (result.error) {
    pageInfo.textContent = result.error;
  }
}

async function nextPage() {
  const result = await window.pywebview.api.next_page(calculatePageSize());
  applySearchResult(result);
}

async function prevPage() {
  const result = await window.pywebview.api.prev_page(calculatePageSize());
  applySearchResult(result);
}

async function firstPage() {
  const result = await window.pywebview.api.first_page(calculatePageSize());
  applySearchResult(result);
}

async function lastPage() {
  const result = await window.pywebview.api.last_page(calculatePageSize());
  applySearchResult(result);
}

// ウィンドウのリサイズ（手動ドラッグ・サイズ選択どちらでも）のたびに、
// プレイヤーの高さ（幅から16:9で算出）と検索結果の表示件数を再計算する
let resizeTimer = null;
async function refreshForResize() {
  applyPlayerHeight();
  if (!hasActiveSearch) return;
  const newSize = calculatePageSize();
  if (newSize === lastAppliedPageSize) return;
  const result = await window.pywebview.api.first_page(newSize);
  applySearchResult(result);
}
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(refreshForResize, 300);
});

// ----------------------------------------------------------------------
// ニコニコ動画: コメント表示のON/OFF
// （埋め込みプレーヤーの非公式JS API。iframeのsrcに jsapi=1 と
//  playerId を付けておくと、postMessage経由でコマンドを受け付ける）
// ----------------------------------------------------------------------
const NICO_EMBED_ORIGIN = "https://embed.nicovideo.jp";
const NICO_PLAYER_ID = "nicoside-player";
let nicoCommentVisible = true;

function setupNicoEmbedMessageListener() {
  window.addEventListener("message", (event) => {
    if (event.origin !== NICO_EMBED_ORIGIN) return;
    const msg = event.data || {};
    // 調査用: 受け取ったイベントの内容をそのままログファイルにも残す
    // （devtoolsを開かなくても後から確認できるようにするため）
    try {
      window.pywebview.api.debug_log("nico embed message: " + JSON.stringify(msg));
    } catch (e) {
      /* ログ送信自体の失敗は無視 */
    }
    // jsapi=1 を付けて埋め込むと、通常のiframe埋め込みと違い
    // プレーヤー側は自動再生をしなくなり、埋め込み側から明示的に
    // "play" コマンドを送るまで再生が始まらない仕様になっている。
    // 読み込み完了(loadComplete)のタイミングで再生開始と、現在の
    // コメント表示設定を送る（新しい動画を読み込むたびにプレーヤー側の
    // 状態はデフォルトにリセットされるため、毎回送り直す）。
    if (msg.eventName === "loadComplete") {
      // 2026-08-14の調査で、明示的にplayコマンドを送るとその直後に
      // unexpected_error が発生することが判明したため、いったん送信を
      // 停止し、プレーヤー自身の自動再生に任せる形にして様子を見る。
      // startNicoPlayback();
      applyNicoCommentVisibility();
    } else if (msg.eventName === "error") {
      const data = msg.data || {};
      const detail = [data.code, data.message].filter(Boolean).join(" / ") || "不明なエラー";
      window.alert("ニコニコ動画の再生でエラーが発生しました: " + detail);
    }
  });
}

function startNicoPlayback() {
  const frame = document.getElementById("player-frame");
  if (!frame || !frame.contentWindow || frame.style.display === "none") return;
  frame.contentWindow.postMessage(
    { sourceConnectorType: 1, playerId: NICO_PLAYER_ID, eventName: "play" },
    NICO_EMBED_ORIGIN
  );
}

function applyNicoCommentVisibility() {
  const frame = document.getElementById("player-frame");
  if (!frame || !frame.contentWindow || frame.style.display === "none") return;
  frame.contentWindow.postMessage(
    {
      sourceConnectorType: 1,
      playerId: NICO_PLAYER_ID,
      eventName: "commentVisibilityChange",
      data: { commentVisibility: nicoCommentVisible },
    },
    NICO_EMBED_ORIGIN
  );
}

function updateCommentUI() {
  const btn = document.getElementById("comment-btn");
  const label = document.getElementById("comment-state");
  btn.classList.toggle("comment-on", nicoCommentVisible);
  label.textContent = nicoCommentVisible ? "ON" : "OFF";
  btn.title = nicoCommentVisible
    ? "ニコニコ動画のコメント表示: ON（クリックで非表示）"
    : "ニコニコ動画のコメント表示: OFF（クリックで表示）";
}

function toggleNicoComment() {
  nicoCommentVisible = !nicoCommentVisible;
  updateCommentUI();
  applyNicoCommentVisibility();
}

// ----------------------------------------------------------------------
// 再生
// ----------------------------------------------------------------------
async function playVideo(videoId) {
  currentVideoId = videoId;
  currentListIndex = currentListItems.indexOf(videoId);

  if (currentProvider === "ニコニコ動画") {
    await playViaNicoScriptEmbed(videoId);
  } else if (currentProvider === "YouTube" && repeatMode === "list") {
    await playViaYouTubePlayerApi(videoId);
  } else {
    await playViaIframe(videoId, repeatMode === "single");
  }
}

// ニコニコ動画のscript埋め込み（#nico-script-embed-mount）を確実に破棄する。
// 中身のscriptタグが生成したiframeは、中身を空にするだけでは再生が
// 止まらない可能性があるため、要素ごと非表示にした上で innerHTML を
// 空にして完全に破棄する。
function destroyNicoScriptEmbedIfAny() {
  const mount = document.getElementById("nico-script-embed-mount");
  if (!mount) return;
  mount.style.display = "none";
  mount.innerHTML = "";
  document.getElementById("nico-watch-external-row").classList.add("hidden");
}

async function playViaIframe(videoId, loop) {
  destroyYtPlayerIfAny();
  destroyNicoScriptEmbedIfAny();
  const url = await window.pywebview.api.get_embed_url(currentProvider, videoId, loop);
  const frame = document.getElementById("player-frame");
  const placeholder = document.getElementById("player-placeholder");
  if (!url) return;
  placeholder.style.display = "none";
  frame.style.display = "block";
  // ニコニコ動画再生時に no-referrer へ切り替えている可能性があるため、
  // 通常のプロバイダ(YouTube等)ではブラウザの既定に戻す。
  // （YouTube側は逆にRefererが無いと再生エラー(153)になるため必須）
  frame.referrerPolicy = "strict-origin-when-cross-origin";
  frame.src = url;
}

// ニコニコ動画: 埋め込み再生の経緯まとめ（詳細は README/HANDOFF 参照）
// 1. 直接 <iframe src="https://embed.nicovideo.jp/watch/{id}"> → unexpected_error
// 2. 公式の埋め込みscriptタグ方式 → CloudFront側で403エラー
// 3. 403の原因（scriptタグが生成するiframeのURLに含まれる referer=
//    クエリパラメータの値に "http://" というスキーム部分が含まれると
//    拒否される）をユーザーの手動検証で特定
// 4. 実際の原因は referer= クエリパラメータではなく、埋め込みプレーヤー
//    内部のスクリプトが独自に参照している document.referrer 側だったと
//    判明し、frame.referrerPolicy を動画ごとに切り替える方式で解決、
//    現在の persistence=1&oldScript=1&from=0&allowProgrammaticFullScreen=1
//    という組み合わせで再生に成功している
// 5. リストリピート対応のため jsapi=1 を再検証したが、oldScriptとの
//    組み合わせ・単体のどちらでもコメント取得自体が失敗し不採用
// 6. プレーヤー自体の「リピート再生」設定（本家視聴ページの動画プレーヤー
//    設定パネルにあるもの）も、埋め込み版では効果が無いことを実機確認
// 7. 本家の視聴ページ（www.nicovideo.jp/watch/{id}）自体を直接iframeに
//    読み込む方式も、クリックジャッキング対策で明確にブロックされ不採用
// 8. 動画の長さ(秒数)を使い、経過後にiframeごと読み込み直す「タイマー
//    方式」も試したが、ブラウザの自動再生ポリシーにより読み込み直す
//    たびに再生が一時停止状態になり、毎回手動で再生ボタンを押す必要が
//    あることが実機で判明。一時停止・シーク操作で不自然にズレる点も
//    含めて2026-08-16に不採用と判断し、コードを削除した。
//
// 結論: 現時点で、埋め込みプレーヤーでの単一曲リピート・リストリピートを
// 実現する現実的な手段は見つかっていない（今後もこの方向性の再提案は
// 行わない）。
async function playViaNicoScriptEmbed(videoId) {
  destroyYtPlayerIfAny();

  const placeholder = document.getElementById("player-placeholder");
  const frame = document.getElementById("player-frame");
  const ytMount = document.getElementById("yt-player-mount");
  const mount = document.getElementById("nico-script-embed-mount");
  const externalRow = document.getElementById("nico-watch-external-row");
  const externalLink = document.getElementById("nico-watch-external-link");

  ytMount.style.display = "none";
  mount.style.display = "none";
  mount.innerHTML = "";
  placeholder.style.display = "none";

  const embedUrl =
    `https://embed.nicovideo.jp/watch/${videoId}` +
    `?persistence=1&oldScript=1&from=0&allowProgrammaticFullScreen=1`;

  window.pywebview.api.debug_log("playViaNicoScriptEmbed: nico embedUrl=" + embedUrl);

  frame.style.display = "block";
  frame.referrerPolicy = "no-referrer";
  frame.src = embedUrl;

  // 万が一まだ失敗する場合に備え、外部ブラウザで確実に見られるリンクも
  // 引き続き表示しておく
  externalLink.onclick = (e) => {
    e.preventDefault();
    window.pywebview.api.open_external(`https://www.nicovideo.jp/watch/${videoId}`);
  };
  externalRow.classList.remove("hidden");
}

async function playViaYouTubePlayerApi(videoId) {
  if (!ytApiReady) {
    // YouTube側のAPIスクリプトがまだ読み込めていない場合は、
    // ひとまず通常のiframe再生にフォールバックする
    // （この場合、動画終了時の自動送りは働かない）
    await playViaIframe(videoId, false);
    return;
  }
  document.getElementById("player-placeholder").style.display = "none";
  destroyNicoScriptEmbedIfAny();

  // 通常再生（<iframe id="player-frame">）からリストリピートに切り替えた
  // 直後は、iframe側に前の動画が読み込まれたまま残っている。
  // display:none にするだけでは見た目上隠れるだけで再生自体は止まらず、
  // 新しく起動するIFrame Player APIの再生と音声が二重に鳴ってしまう。
  // src を空にして読み込みを解除することで、確実に前の再生を停止する。
  const frame = document.getElementById("player-frame");
  frame.style.display = "none";
  frame.src = "";

  const mount = document.getElementById("yt-player-mount");
  mount.style.display = "block";

  if (ytPlayer && typeof ytPlayer.loadVideoById === "function") {
    ytPlayer.loadVideoById(videoId);
    return;
  }
  ytPlayer = new YT.Player("yt-player-mount", {
    videoId: videoId,
    playerVars: { autoplay: 1, enablejsapi: 1 },
    events: { onStateChange: onYouTubePlayerStateChange },
  });
}

function onYouTubePlayerStateChange(event) {
  // YT.PlayerState.ENDED === 0
  if (event.data === 0 && repeatMode === "list") {
    advanceListRepeat();
  }
}

function advanceListRepeat() {
  if (!currentListItems.length) return;
  let nextIndex = currentListIndex + 1;
  if (nextIndex >= currentListItems.length) nextIndex = 0; // 末尾まで行ったら先頭に戻る
  const nextId = currentListItems[nextIndex];
  currentListIndex = nextIndex;
  currentVideoId = nextId;
  if (ytPlayer && typeof ytPlayer.loadVideoById === "function") {
    ytPlayer.loadVideoById(nextId);
  }
}

function destroyYtPlayerIfAny() {
  if (ytPlayer) {
    try {
      ytPlayer.destroy();
    } catch (e) {
      /* noop */
    }
    ytPlayer = null;
  }
  const mount = document.getElementById("yt-player-mount");
  if (mount) mount.style.display = "none";
}

// ----------------------------------------------------------------------
// リピートモード（リピートしない / 1曲リピート / リストリピート）
// ----------------------------------------------------------------------
function updateRepeatUI() {
  const btn = document.getElementById("repeat-btn");
  const label = document.getElementById("repeat-state");
  btn.classList.remove("repeat-single", "repeat-list");
  if (repeatMode === "single") {
    btn.classList.add("repeat-single");
    label.textContent = "1曲";
    btn.title = "リピート再生: 1曲リピート中（クリックで設定を変更）";
  } else if (repeatMode === "list") {
    btn.classList.add("repeat-list");
    label.textContent = "全";
    btn.title = "リピート再生: リストリピート中（YouTubeのみ・表示中のページが対象。クリックで設定を変更）";
  } else {
    label.textContent = "OFF";
    btn.title = "リピート再生: OFF（クリックで設定を変更）";
  }

  document.querySelectorAll("#repeat-menu .popup-menu-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.mode === repeatMode);
  });
}

function toggleRepeatMenu(show) {
  const menu = document.getElementById("repeat-menu");
  if (show === undefined) {
    menu.classList.toggle("hidden");
  } else {
    menu.classList.toggle("hidden", !show);
  }
}

async function selectRepeatMode(mode) {
  repeatMode = mode;
  updateRepeatUI();
  toggleRepeatMenu(false);
  // 再生中の動画があれば、新しいリピート設定を反映して再読み込みする
  if (currentVideoId) {
    await playVideo(currentVideoId);
  }
}

function updatePinUI(mode) {
  const btn = document.getElementById("pin-btn");
  const label = document.getElementById("pin-state");
  btn.classList.remove("pin-on", "pin-auto");
  if (mode === "on") {
    btn.classList.add("pin-on");
    label.textContent = "ON";
    btn.title = "最前面表示: 常に最前面（クリックで設定を変更）";
  } else if (mode === "auto") {
    btn.classList.add("pin-auto");
    label.textContent = "自動";
    btn.title = "最前面表示: 自動的に隠す（クリックで設定を変更）";
  } else {
    label.textContent = "OFF";
    btn.title = "最前面表示: OFF（クリックで設定を変更）";
  }
  document.querySelectorAll("#pin-menu .popup-menu-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.mode === mode);
  });
}

function togglePinMenu(show) {
  const menu = document.getElementById("pin-menu");
  if (show === undefined) {
    menu.classList.toggle("hidden");
  } else {
    menu.classList.toggle("hidden", !show);
  }
}

async function selectPinMode(mode) {
  const result = await window.pywebview.api.set_pin_mode(mode);
  pinMode = result.mode;
  updatePinUI(pinMode);
  togglePinMenu(false);
  resetAutoHideTimer();
}

// ----------------------------------------------------------------------
// 自動的に隠すモード:
// マウスがウィンドウから一定時間離れると、画面端に「にゅるっと」畳まれる。
// 畳まれた細いバーにマウスが近づく（＝ウィンドウにカーソルが入る）と、
// 同様に「にゅるっと」元のサイズへ戻る。
// ----------------------------------------------------------------------
let pinMode = "off";
let autoHideTimer = null;
const AUTO_HIDE_DELAY_MS = 3000;

function resetAutoHideTimer() {
  if (autoHideTimer) {
    clearTimeout(autoHideTimer);
    autoHideTimer = null;
  }
  if (pinMode === "auto") {
    autoHideTimer = setTimeout(async () => {
      await window.pywebview.api.collapse_window();
    }, AUTO_HIDE_DELAY_MS);
  }
}

async function handleWindowMouseEnter() {
  if (autoHideTimer) {
    clearTimeout(autoHideTimer);
    autoHideTimer = null;
  }
  const collapsed = await window.pywebview.api.is_window_collapsed();
  if (collapsed) {
    await window.pywebview.api.expand_window();
  }
}

function handleWindowMouseLeave() {
  resetAutoHideTimer();
}

function updateSizeUI(multiplier) {
  const btn = document.getElementById("size-btn");
  const label = document.getElementById("size-state");
  label.textContent = multiplier + "x";
  btn.classList.remove("size-2x", "size-3x");
  if (multiplier === 2) btn.classList.add("size-2x");
  if (multiplier === 3) btn.classList.add("size-3x");

  document.querySelectorAll("#size-menu .popup-menu-item").forEach((el) => {
    el.classList.toggle("active", Number(el.dataset.multiplier) === multiplier);
  });
}

function toggleSizeMenu(show) {
  const menu = document.getElementById("size-menu");
  if (show === undefined) {
    menu.classList.toggle("hidden");
  } else {
    menu.classList.toggle("hidden", !show);
  }
}

async function selectWindowSize(multiplier) {
  const result = await window.pywebview.api.select_window_size(multiplier);
  updateSizeUI(result.multiplier);
  toggleSizeMenu(false);
  // 実際のウィンドウリサイズは非同期にOS側で行われるため、
  // 少し待ってから幅を再取得してプレイヤー高さ・ページサイズを更新する
  setTimeout(refreshForResize, 250);
}

function closeAllPopupMenus() {
  toggleSizeMenu(false);
  toggleRepeatMenu(false);
  toggleAccountMenu(false);
  toggleNicoAccountMenu(false);
  togglePinMenu(false);
}

waitForPywebview(async () => {
  loadYouTubeIframeAPI();
  setupNicoEmbedMessageListener();
  updateCommentUI();
  await loadProviders();
  await onProviderChanged();

  document.getElementById("search-btn").addEventListener("click", performSearch);
  document.getElementById("search-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") performSearch();
  });
  document.getElementById("provider-select").addEventListener("change", onProviderChanged);
  document.getElementById("sort-select").addEventListener("change", onSortChanged);
  document.getElementById("playlist-select").addEventListener("change", onPlaylistSelected);
  document.getElementById("mylist-add-btn").addEventListener("click", addNiconicoMylist);
  document.getElementById("mylist-remove-btn").addEventListener("click", removeNiconicoMylist);
  document.getElementById("comment-btn").addEventListener("click", toggleNicoComment);

  document.getElementById("pin-btn").addEventListener("click", (e) => {
    e.stopPropagation();
    closeAllPopupMenus();
    togglePinMenu();
  });
  document.querySelectorAll("#pin-menu .popup-menu-item").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      selectPinMode(el.dataset.mode);
    });
  });

  document.getElementById("account-btn").addEventListener("click", (e) => {
    e.stopPropagation();
    closeAllPopupMenus();
    toggleAccountMenu();
  });
  document.querySelectorAll("#account-menu .popup-menu-item").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      handleAccountMenuAction(el.dataset.action);
    });
  });

  document.getElementById("nico-account-btn").addEventListener("click", (e) => {
    e.stopPropagation();
    closeAllPopupMenus();
    toggleNicoAccountMenu();
  });
  document.querySelectorAll("#nico-account-menu .popup-menu-item").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      handleNicoAccountMenuAction(el.dataset.action);
    });
  });

  document.getElementById("repeat-btn").addEventListener("click", (e) => {
    e.stopPropagation();
    closeAllPopupMenus();
    toggleRepeatMenu();
  });
  document.querySelectorAll("#repeat-menu .popup-menu-item").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      selectRepeatMode(el.dataset.mode);
    });
  });

  document.getElementById("size-btn").addEventListener("click", (e) => {
    e.stopPropagation();
    closeAllPopupMenus();
    toggleSizeMenu();
  });
  document.querySelectorAll("#size-menu .popup-menu-item").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      selectWindowSize(Number(el.dataset.multiplier));
    });
  });

  document.addEventListener("click", () => closeAllPopupMenus());

  document.getElementById("first-btn").addEventListener("click", firstPage);
  document.getElementById("prev-btn").addEventListener("click", prevPage);
  document.getElementById("next-btn").addEventListener("click", nextPage);
  document.getElementById("last-btn").addEventListener("click", lastPage);

  // 自動的に隠すモード用: マウスがウィンドウに出入りしたタイミングを検知する
  document.documentElement.addEventListener("mouseenter", handleWindowMouseEnter);
  document.documentElement.addEventListener("mouseleave", handleWindowMouseLeave);

  pinMode = await window.pywebview.api.get_pin_mode();
  updatePinUI(pinMode);
  resetAutoHideTimer();
  updateRepeatUI();
  updateSizeUI(1);
  applyPlayerHeight();
  await refreshAccountStatus();
});
