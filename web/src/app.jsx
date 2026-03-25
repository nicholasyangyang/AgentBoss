import { useState, useEffect } from 'preact/hooks';
import { Navbar } from './components/Navbar.jsx';
import { JobList } from './components/JobList.jsx';
import { PublishForm } from './components/PublishForm.jsx';
import { DeleteModal } from './components/DeleteModal.jsx';
import { ToastContainer } from './components/ToastContainer.jsx';
import { useJobs } from './hooks/useJobs.js';
import { useFavorites } from './hooks/useFavorites.js';
import { useDeletedJobs } from './hooks/useDeletedJobs.js';
import { useToast } from './hooks/useToast.js';
import { hasSigner, deleteJob } from './lib/nostr.js';
import { useAuth } from './hooks/useAuth.js';
import { t, getLang, subscribeToLang } from './lib/i18n.js';

export function App() {
  const [searchQuery, setSearchQuery] = useState('');
  const [showPublish, setShowPublish] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [view, setView] = useState('all');
  const [editingJob, setEditingJob] = useState(null);
  // Force re-render when language changes via subscribeToLang
  const [_langVersion, setLangVersion] = useState(0);
  const { jobs, loading, error, reload } = useJobs({ searchQuery });
  const { count: favCount } = useFavorites();
  const { pubkey } = useAuth();
  const { markDeleted } = useDeletedJobs();
  const { toasts, showToast, dismissToast } = useToast();

  const myJobs = pubkey ? jobs.filter((j) => j.pubkey === pubkey) : [];

  // Subscribe to language changes — triggers re-render on switch
  useEffect(() => {
    const unsub = subscribeToLang(() => setLangVersion((v) => v + 1));
    return unsub;
  }, []);

  // Read language once to keep unused variable lint happy
  void getLang();

  const handlePublishClick = () => {
    if (!hasSigner()) {
      showToast(t('toast_nip07_err'), 'error');
      return;
    }
    setShowPublish(true);
  };

  const handlePublishSuccess = () => {
    showToast(t('toast_published'), 'success');
    setTimeout(() => setShowPublish(false), 2500);
  };

  const handleDeleteConfirm = async (job) => {
    try {
      await deleteJob(job.d_tag, pubkey);
      markDeleted(job.d_tag);
      showToast(t('toast_deleted'), 'success');
    } catch {
      showToast(t('toast_delete_err'), 'error');
    }
    setDeleteTarget(null);
  };

  const handleEditSuccess = () => {
    showToast(t('toast_edited'), 'success');
    setEditingJob(null);
    reload();
  };

  return (
    <div class="page">
      {/* Banner */}
      <div class="banner">
        <span class="banner-dot" />
        {t('banner_text')}
        <span class="banner-dot" />
        {t('banner_sub')}
        <span class="banner-dot" />
      </div>

      {/* Navbar */}
      <Navbar
        onSearch={setSearchQuery}
        onPublish={handlePublishClick}
      />

      {/* View Tabs */}
      {hasSigner() && (
        <div style="display: flex; justify-content: center; padding: 12px 0 0;">
          <div class="navbar-tabs">
            <button
              class={`navbar-tab ${view === 'all' ? 'active' : ''}`}
              onClick={() => setView('all')}
            >
              {t('latest_jobs')}
            </button>
            <button
              class={`navbar-tab ${view === 'mine' ? 'active' : ''}`}
              onClick={() => setView('mine')}
            >
              {t('my_jobs_tab')}
            </button>
          </div>
        </div>
      )}

      {/* Main Content */}
      <main class="main-content">
        <div class="container">
          {/* Hero */}
          <section class="hero">
            <p class="hero-eyebrow">{t('hero_eyebrow')}</p>
            <h1 dangerouslySetInnerHTML={{ __html: t('hero_title') }} />
            <p class="hero-desc">{t('hero_desc')}</p>
          </section>

          {/* Jobs Layout */}
          <div class="jobs-layout">
            {/* Job Feed */}
            <div>
              {view === 'mine' ? (
                <>
                  <div class="jobs-section-title">
                    <span>{t('my_jobs_tab')}</span>
                    <span style="color: var(--text-muted); font-size: 11px">
                      {loading ? t('loading_jobs') : `${myJobs.length} ${t('jobs_count')}`}
                    </span>
                  </div>
                  <JobList
                    jobs={myJobs}
                    loading={loading}
                    error={error}
                    onJobClick={() => {}}
                    onRetry={reload}
                    onPublish={handlePublishClick}
                    onDelete={(j) => setDeleteTarget(j)}
                    onEdit={(j) => setEditingJob(j)}
                  />
                  {!loading && myJobs.length === 0 && (
                    <div class="empty-state">
                      <div class="empty-state-icon">📝</div>
                      <h3>{t('no_my_jobs')}</h3>
                      <button class="btn btn-primary" onClick={handlePublishClick}>
                        {t('publish_btn')}
                      </button>
                    </div>
                  )}
                </>
              ) : (
                <>
                  <div class="jobs-section-title">
                    <span>{t('latest_jobs')}</span>
                    <span style="color: var(--text-muted); font-size: 11px">
                      {loading ? t('loading_jobs') : `${jobs.length} ${t('jobs_count')}`}
                    </span>
                  </div>
                  <JobList
                    jobs={jobs}
                    loading={loading}
                    error={error}
                    onJobClick={() => {}}
                    onRetry={reload}
                    onPublish={handlePublishClick}
                    onDelete={pubkey ? (j) => setDeleteTarget(j) : undefined}
                    onEdit={pubkey ? (j) => setEditingJob(j) : undefined}
                  />
                </>
              )}
            </div>

            {/* Sidebar */}
            <aside class="sidebar">
              {/* Stats Widget */}
              <div class="sidebar-widget">
                <div class="sidebar-title">{t('data_overview')}</div>
                <div class="stats-grid">
                  <div class="stat-item">
                    <div class="stat-value">{jobs.length}</div>
                    <div class="stat-label">{t('jobs')}</div>
                  </div>
                  <div class="stat-item">
                    <div class="stat-value">{favCount}</div>
                    <div class="stat-label">{t('favorites')}</div>
                  </div>
                  {pubkey && (
                    <div class="stat-item">
                      <div class="stat-value">{myJobs.length}</div>
                      <div class="stat-label">{t('my_jobs')}</div>
                    </div>
                  )}
                </div>
              </div>

              {/* Tags Widget */}
              <div class="sidebar-widget">
                <div class="sidebar-title">{t('popular_tags')}</div>
                <div class="tag-cloud">
                  <button class="cloud-tag" onClick={() => setSearchQuery('remote')}>🌍 Remote</button>
                  <button class="cloud-tag" onClick={() => setSearchQuery('engineering')}>💻 Engineering</button>
                  <button class="cloud-tag" onClick={() => setSearchQuery('beijing')}>🏙 Beijing</button>
                  <button class="cloud-tag" onClick={() => setSearchQuery('shanghai')}>🏙 Shanghai</button>
                  <button class="cloud-tag" onClick={() => setSearchQuery('30k')}>💰 30k+</button>
                  <button class="cloud-tag" onClick={() => setSearchQuery('senior')}>⭐ Senior</button>
                  <button class="cloud-tag" onClick={() => setSearchQuery('frontend')}>⚛ Frontend</button>
                  <button class="cloud-tag" onClick={() => setSearchQuery('design')}>🎨 Design</button>
                </div>
              </div>

              {/* NIP-07 Info */}
              {!hasSigner() && (
                <div class="sidebar-widget" style="border-color: var(--accent-border)">
                  <div class="sidebar-title" style="color: var(--accent)">
                    {t('need_nip07')}
                  </div>
                  <p style="font-size: 12px; color: var(--text-muted); line-height: 1.6; margin-bottom: 12px">
                    {t('install_ext')}
                  </p>
                  <div style="display: flex; flex-direction: column; gap: 8px;">
                    <a
                      href="https://alby.com"
                      target="_blank"
                      rel="noopener"
                      class="btn btn-ghost"
                      style="font-size: 12px; justify-content: center;"
                    >
                      Alby（推荐）
                    </a>
                    <a
                      href="https://nos2x.kkreunt.com"
                      target="_blank"
                      rel="noopener"
                      class="btn btn-ghost"
                      style="font-size: 12px; justify-content: center;"
                    >
                      nos2x
                    </a>
                  </div>
                </div>
              )}
            </aside>
          </div>
        </div>
      </main>

      {/* Footer */}
      <footer class="footer">
        {t('footer_text')} ·
        <a href="https://github.com/nicholasyangyang/AgentBoss" target="_blank" rel="noopener">
          {t('github')}
        </a>
      </footer>

      {/* Publish Modal */}
      {showPublish && (
        <PublishForm
          onClose={() => setShowPublish(false)}
          onSuccess={handlePublishSuccess}
        />
      )}

      {/* Edit Modal — mutually exclusive with DeleteModal */}
      {editingJob && !deleteTarget && (
        <PublishForm
          jobToEdit={editingJob}
          onClose={() => setEditingJob(null)}
          onSuccess={handleEditSuccess}
        />
      )}

      {/* Delete Modal */}
      {deleteTarget && (
        <DeleteModal
          job={deleteTarget}
          onConfirm={() => handleDeleteConfirm(deleteTarget)}
          onClose={() => setDeleteTarget(null)}
        />
      )}

      {/* Toast Container */}
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}
