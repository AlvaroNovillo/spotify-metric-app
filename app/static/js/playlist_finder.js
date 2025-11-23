/**
 * Playlist Finder Logic
 * Handles search, filtering, email generation, and downloads.
 */

// --- Global State ---
let currentArtistId = null;
let allFoundPlaylists = []; // Master list of all playlists found
let emailContentVariations = {};

// --- Initialization ---
document.addEventListener('DOMContentLoaded', () => {
    // Initialize state from DOM
    const container = document.getElementById('playlist-finder-container');
    if (container) {
        currentArtistId = container.dataset.artistId;
    }

    // Event Listeners
    const contactBtn = document.getElementById('contact-curators-button');
    if (contactBtn) contactBtn.addEventListener('click', showEmailOverlay);

    const downloadBtn = document.getElementById('show-download-modal-btn');
    if (downloadBtn) downloadBtn.addEventListener('click', showPlaylistDownloadModal);

    const generateEmailBtn = document.getElementById('generate-email-button');
    if (generateEmailBtn) generateEmailBtn.addEventListener('click', generateEmailAction);

    const confirmSendBtn = document.getElementById('confirm-send-button');
    if (confirmSendBtn) confirmSendBtn.addEventListener('click', confirmAndSendEmails);

    const cancelEmailBtn = document.getElementById('cancel-email-button');
    if (cancelEmailBtn) cancelEmailBtn.addEventListener('click', cancelEmailSend);

    const templateEditor = document.getElementById('template-body-editor');
    if (templateEditor) templateEditor.addEventListener('input', updateLivePreview);

    const cancelDownloadBtn = document.getElementById('cancel-download-button');
    if (cancelDownloadBtn) cancelDownloadBtn.addEventListener('click', hidePlaylistDownloadModal);

    document.querySelectorAll('.download-playlist-btn').forEach(button => {
        button.addEventListener('click', () => generateAndDownloadFile(button.dataset.format));
    });

    // Search Form Listener
    const findForm = document.getElementById('playlist-finder-form');
    const findButton = document.getElementById('find-playlists-button');

    if (findForm && findButton) {
        findForm.addEventListener('submit', function (event) {
            if (!findForm.querySelector('input[name="selected_track_id"]:checked')) {
                alert("Please select a track before finding playlists.");
                event.preventDefault();
                return;
            }

            // Set loading state
            findButton.disabled = true;
            const btnText = findButton.querySelector('.button-text');
            const btnLoading = findButton.querySelector('.button-loading');
            if (btnText) btnText.classList.add('hidden');
            if (btnLoading) btnLoading.classList.remove('hidden');
        });
    }

    // Initialize Upload Feature
    setupUploadFeature();
});

// --- Helper Functions ---

function getVisiblePlaylists() {
    const visiblePlaylistIds = new Set();
    document.querySelectorAll('.playlist-card').forEach(card => {
        if (card.style.display !== 'none') {
            visiblePlaylistIds.add(card.dataset.playlistId);
        }
    });
    return allFoundPlaylists.filter(p => visiblePlaylistIds.has(p.id));
}

// --- UI Feedback ---

function updateProgress(percentage, statusText) {
    const progressBar = document.getElementById('progress-bar');
    const progressStatus = document.getElementById('progress-status');
    const container = document.getElementById('progress-container');

    if (container) container.style.display = 'block';
    if (progressBar) progressBar.style.width = percentage + '%';

    if (progressStatus) {
        // Escape HTML to prevent XSS
        const displayStatus = statusText.replace(/</g, "&lt;").replace(/>/g, "&gt;");
        progressStatus.innerHTML = `<span style="color: var(--success); font-weight: 600;">${displayStatus}</span> (${percentage}%)`;
    }
}

function hideProgress() {
    const progressContainer = document.getElementById('progress-container');
    if (progressContainer) progressContainer.style.display = 'none';

    const findBtn = document.getElementById('find-playlists-button');
    if (findBtn) {
        findBtn.disabled = false;
        const buttonText = findBtn.querySelector('.button-text');
        const buttonLoading = findBtn.querySelector('.button-loading');
        if (buttonText) buttonText.classList.remove('hidden');
        if (buttonLoading) buttonLoading.classList.add('hidden');
    }
}

function showSearchError(errorMessage) {
    hideProgress();
    const resultsContainer = document.getElementById('playlist-results-container');
    const displayError = errorMessage.replace(/</g, "&lt;").replace(/>/g, "&gt;");

    if (resultsContainer) {
        resultsContainer.innerHTML = `
            <div class="card" style="border-color: var(--error); background-color: rgba(239, 68, 68, 0.1);">
                <h3 style="color: var(--error); margin-bottom: 0.5rem;">Search Error</h3>
                <p>${displayError}</p>
            </div>`;
    }

    const postSearchActions = document.getElementById('post-search-actions');
    if (postSearchActions) postSearchActions.classList.add('hidden');
}

function updateKeywordsDisplay(keywords) {
    const displayArea = document.getElementById('keywords-display-area');
    const listSpan = document.getElementById('keywords-list');
    if (displayArea && listSpan && keywords && keywords.length > 0) {
        listSpan.textContent = keywords.join(', ');
        displayArea.style.display = 'block';
    } else if (displayArea) {
        displayArea.style.display = 'none';
    }
}

// --- Data Injection ---

function injectResultsAndData(resultsHtml, playlistData) {
    const resultsContainer = document.getElementById('playlist-results-container');
    if (resultsContainer) {
        resultsContainer.innerHTML = resultsHtml;
    }

    allFoundPlaylists = playlistData || [];

    updateActionButtons();

    if (allFoundPlaylists.length > 0) {
        setupAiSmartSearch();
        updatePlaylistCount();
    }
}

function updateActionButtons() {
    const actionsContainer = document.getElementById('post-search-actions');
    if (actionsContainer && allFoundPlaylists && allFoundPlaylists.length > 0) {
        actionsContainer.classList.remove('hidden');
    } else if (actionsContainer) {
        actionsContainer.classList.add('hidden');
    }
}

// --- AI Smart Filter ---

function setupAiSmartSearch() {
    const searchInput = document.getElementById('ai-smart-search');
    const applyButton = document.getElementById('ai-filter-button');
    const statusEl = document.getElementById('ai-filter-status');

    if (!searchInput || !applyButton || !statusEl) return;

    const applyFilter = async () => {
        const query = searchInput.value.trim();
        if (!query) {
            document.querySelectorAll('.playlist-card').forEach(card => card.style.display = 'flex');
            updatePlaylistCount();
            statusEl.textContent = "Filter cleared.";
            return;
        }

        const btnText = applyButton.querySelector('.button-text');
        const btnLoading = applyButton.querySelector('.button-loading');
        applyButton.disabled = true;
        btnText.classList.add('hidden');
        btnLoading.classList.remove('hidden');
        statusEl.textContent = "AI is analyzing your results...";
        statusEl.style.color = 'var(--text-muted)';

        try {
            const response = await fetch("/playlists/filter_ai", { // Hardcoded path or inject via data-url
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                body: JSON.stringify({ query: query, playlists: allFoundPlaylists })
            });

            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.error || `Server error ${response.status}`);
            }

            const result = await response.json();
            const matchingIds = new Set(result.playlist_ids || []);
            let visibleCount = 0;

            document.querySelectorAll('.playlist-card').forEach(card => {
                if (matchingIds.has(card.dataset.playlistId)) {
                    card.style.display = 'flex';
                    visibleCount++;
                } else {
                    card.style.display = 'none';
                }
            });

            updatePlaylistCount(visibleCount);
            statusEl.textContent = `AI filter applied. Found ${visibleCount} matching playlists.`;

        } catch (error) {
            console.error("AI Filter Error:", error);
            statusEl.textContent = `Error: ${error.message}`;
            statusEl.style.color = 'var(--error)';
        } finally {
            applyButton.disabled = false;
            btnText.classList.remove('hidden');
            btnLoading.classList.add('hidden');
        }
    };

    applyButton.addEventListener('click', applyFilter);
    searchInput.addEventListener('keyup', (event) => {
        if (event.key === 'Enter') {
            event.preventDefault();
            applyFilter();
        }
    });
}

function updatePlaylistCount(visibleCount = null) {
    const countDisplay = document.getElementById('playlist-count-display');
    if (!countDisplay) return;
    const totalCount = allFoundPlaylists.length;
    const currentVisible = visibleCount !== null ? visibleCount : getVisiblePlaylists().length;

    if (currentVisible === totalCount) {
        countDisplay.textContent = `Showing all ${totalCount} playlists.`;
    } else {
        countDisplay.textContent = `Showing ${currentVisible} of ${totalCount} playlists matching your filter.`;
    }
}

// --- Email Modal ---

function showEmailOverlay() {
    const overlay = document.getElementById('email-overlay');
    document.getElementById('song-description-editor').value = '';
    document.getElementById('language-selector').value = 'English';
    document.getElementById('bcc-email-input').value = '';
    document.getElementById('email-preview-edit-section').classList.add('hidden');

    const generateBtn = document.getElementById('generate-email-button');
    generateBtn.disabled = false;
    generateBtn.querySelector('.button-text').classList.remove('hidden');
    generateBtn.querySelector('.button-loading').classList.add('hidden');

    const sendBtn = document.getElementById('confirm-send-button');
    sendBtn.classList.add('hidden');
    sendBtn.disabled = true;

    if (overlay) overlay.classList.remove('hidden');
}

function cancelEmailSend() {
    document.getElementById('email-overlay').classList.add('hidden');
}

function updateLivePreview() {
    // Placeholder for live preview logic if needed locally
    // The server returns the preview, but we can update it if the user edits the template
}

function generateEmailAction() {
    const subjectEditorEl = document.getElementById('preview-subject-editor');
    const previewBodyEl = document.getElementById('preview-body');
    const templateEditorEl = document.getElementById('template-body-editor');
    const descriptionEditor = document.getElementById('song-description-editor');
    const langSelector = document.getElementById('language-selector');
    const generateBtn = document.getElementById('generate-email-button');
    const sendBtn = document.getElementById('confirm-send-button');
    const previewEditSection = document.getElementById('email-preview-edit-section');
    const selectedTrackInput = document.querySelector('input[name="selected_track_id"]:checked');

    const loadingText = generateBtn.querySelector('.button-loading');
    const buttonText = generateBtn.querySelector('.button-text');
    const language = langSelector.value;

    if (!selectedTrackInput) {
        alert('Please select a track first.');
        return;
    }
    const songDescription = descriptionEditor.value.trim();
    if (!songDescription) {
        alert('Please enter a song description for the AI to use.');
        descriptionEditor.focus();
        return;
    }

    const visiblePlaylists = getVisiblePlaylists();
    const firstPlaylist = visiblePlaylists.find(p => p && p.email && p.email.includes('@'));
    if (!firstPlaylist) {
        alert("No contactable playlists are visible with the current filter. Cannot generate a preview.");
        return;
    }

    generateBtn.disabled = true;
    loadingText.classList.remove('hidden');
    buttonText.classList.add('hidden');
    previewEditSection.classList.add('hidden');
    sendBtn.classList.add('hidden');
    sendBtn.disabled = true;

    fetch("/playlists/generate_preview_email", {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify({
            track_id: selectedTrackInput.value,
            song_description: songDescription,
            language: language,
            playlist: firstPlaylist
        })
    })
        .then(response => response.ok ? response.json() : response.json().then(err => Promise.reject(err)))
        .then(data => {
            if (data.error) throw new Error(data.error);
            subjectEditorEl.value = data.subject || '';
            previewBodyEl.textContent = data.preview_body || '';
            templateEditorEl.value = data.template_body || '';
            emailContentVariations = data.variations || {};
            previewEditSection.classList.remove('hidden');
            sendBtn.classList.remove('hidden');
            sendBtn.disabled = false;
        })
        .catch(error => {
            console.error("Error fetching email preview:", error);
            subjectEditorEl.value = 'Error';
            previewBodyEl.textContent = `Failed to generate: ${error.message || 'Unknown error'}`;
            templateEditorEl.value = `An error occurred: ${error.message || 'Unknown error'}`;
        })
        .finally(() => {
            generateBtn.disabled = false;
            loadingText.classList.add('hidden');
            buttonText.classList.remove('hidden');
        });
}

function confirmAndSendEmails() {
    const emailLimitInput = document.getElementById('email-limit-input');
    const editedSubject = document.getElementById('preview-subject-editor').value.trim();
    const bccEmail = document.getElementById('bcc-email-input').value.trim();
    const editedTemplateBody = document.getElementById('template-body-editor').value.trim();
    const trackIdInput = document.querySelector('input[name="selected_track_id"]:checked');
    const contactButton = document.getElementById('contact-curators-button');
    const emailLog = document.getElementById('email-log');

    if (!trackIdInput) {
        alert("Error: A track is not selected. Please re-select a track.");
        return;
    }
    const trackId = trackIdInput.value;

    let emailLimit = parseInt(emailLimitInput.value, 10);
    if (isNaN(emailLimit) || emailLimit < 1) {
        alert("Please enter a valid number for the email limit (1 or more).");
        emailLimitInput.focus();
        return;
    }
    if (emailLimit > 300) emailLimit = 300;

    if (!editedSubject || !editedTemplateBody) {
        alert("The subject and the template body cannot be empty.");
        return;
    }
    if (Object.keys(emailContentVariations).length === 0) {
        alert("Email variations have not been generated. Please click 'Generate Template & Preview' first.");
        return;
    }

    const allContactablePlaylists = getVisiblePlaylists().filter(p => p && p.email && p.email.includes('@'));
    if (allContactablePlaylists.length === 0) {
        alert("There are no contactable playlists in the current view to send emails to.");
        return;
    }

    const isLimited = allContactablePlaylists.length > emailLimit;
    const playlistsToContact = allContactablePlaylists.slice(0, emailLimit);

    const confirmationMessage = `You are about to send emails to ${playlistsToContact.length} currently visible contactable playlists.${isLimited ? ` (Limited from ${allContactablePlaylists.length} total)` : ''}\n\nContinue?`;
    if (!confirm(confirmationMessage)) {
        return;
    }

    cancelEmailSend();
    contactButton.disabled = true;
    contactButton.innerHTML = '<span class="animate-spin">Sending Emails...</span>';
    emailLog.textContent = `Initializing email process for ${playlistsToContact.length} playlists...\n`;
    document.getElementById('email-status-container').style.display = 'block';

    fetch("/playlists/send_emails", {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
        body: JSON.stringify({
            track_id: trackId,
            playlists: playlistsToContact,
            subject: editedSubject,
            variations: emailContentVariations,
            template_body: editedTemplateBody,
            bcc_email: bccEmail
        })
    })
        .then(response => {
            if (!response.ok) throw new Error(`Server error: ${response.status}`);
            const reader = response.body.getReader();
            const decoder = new TextDecoder();

            function processStream({ done, value }) {
                if (done) {
                    contactButton.disabled = false;
                    contactButton.innerHTML = 'Contact Curators...';
                    return;
                }
                const chunk = decoder.decode(value, { stream: true });

                const lines = chunk.split('\n\n');
                lines.forEach(line => {
                    if (line.startsWith('data: ')) {
                        emailLog.textContent += line.substring(6).trim() + '\n';
                    }
                });

                emailLog.scrollTop = emailLog.scrollHeight;
                reader.read().then(processStream);
            }
            reader.read().then(processStream);
        })
        .catch(error => {
            console.error("Error during email sending stream:", error);
            emailLog.textContent += `\n❌ Network or processing error: ${error.message}\n`;
            contactButton.disabled = false;
            contactButton.innerHTML = 'Contact Curators...';
        });
}

// --- Download Modal ---

function showPlaylistDownloadModal() {
    document.getElementById('download-playlist-modal').classList.remove('hidden');
}

function hidePlaylistDownloadModal() {
    document.getElementById('download-playlist-modal').classList.add('hidden');
}

function generateAndDownloadFile(format) {
    const playlistsToDownload = getVisiblePlaylists();
    if (playlistsToDownload.length === 0) {
        alert("There are no playlists in the current view to download."); return;
    }

    const selectedColumns = Array.from(document.querySelectorAll('#download-playlist-form input[name="columns"]:checked')).map(cb => cb.value);
    if (selectedColumns.length === 0) {
        alert('Please select at least one column to export.'); return;
    }

    const columnHeaders = {
        name: 'Playlist Name', url: 'Spotify URL', owner_name: 'Curator Name', email: 'Curator Email',
        followers: 'Followers', tracks_total: 'Total Tracks', description: 'Description',
        found_by: 'Found By Keyword', contacted: 'Contacted'
    };

    const dataForExport = playlistsToDownload.map(pl => {
        let row = {};
        selectedColumns.forEach(colKey => {
            let value = pl[colKey];
            if (colKey === 'contacted') {
                value = pl.contacted || 0;
            }
            if (Array.isArray(value)) value = value.join(', ');
            row[columnHeaders[colKey]] = value ?? '';
        });
        return row;
    });

    const trackNameInput = document.querySelector('input[name="selected_track_id"]:checked');
    const trackName = trackNameInput ? trackNameInput.closest('.song-item-label').dataset.trackName : 'selected_track';
    const safeTrackName = trackName.replace(/[^a-z0-9]/gi, '_').toLowerCase();
    const filename = `playlists_for_${safeTrackName}.${format}`;

    let blob;
    if (format === 'csv') {
        const csvContent = convertToCSV(dataForExport);
        blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    } else {
        const worksheet = XLSX.utils.json_to_sheet(dataForExport);
        const workbook = XLSX.utils.book_new();
        XLSX.utils.book_append_sheet(workbook, worksheet, 'Playlists');
        const xlsxData = XLSX.write(workbook, { bookType: 'xlsx', type: 'array' });
        blob = new Blob([xlsxData], { type: 'application/vnd.openxmlformats-officedocument.spreadsheet.sheet' });
    }

    const link = document.createElement("a");
    const url = URL.createObjectURL(blob);
    link.setAttribute("href", url);
    link.setAttribute("download", filename);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);

    hidePlaylistDownloadModal();
}

function convertToCSV(objArray) {
    if (objArray.length === 0) return "";
    const headers = Object.keys(objArray[0]);
    const csvRows = [headers.join(',')];
    for (const row of objArray) {
        const values = headers.map(header => {
            let value = String(row[header]);
            if (value.includes(',') || value.includes('"') || value.includes('\n')) {
                value = '"' + value.replace(/"/g, '""') + '"';
            }
            return value;
        });
        csvRows.push(values.join(','));
    }
    return csvRows.join('\r\n');
}

// --- Upload Feature ---

function setupUploadFeature() {
    const fileInput = document.getElementById('playlist-upload-input');
    const uploadStatus = document.getElementById('upload-status');
    const uploadLabel = document.getElementById('upload-label-text');

    if (!fileInput) return;

    fileInput.addEventListener('change', async (event) => {
        const file = event.target.files[0];
        if (!file) return;

        if (!file.name.match(/\.(xlsx|xls)$/i)) {
            uploadStatus.textContent = "Error: Please select an Excel file.";
            uploadStatus.style.color = 'var(--error)';
            fileInput.value = '';
            return;
        }

        uploadStatus.textContent = `Processing ${file.name}...`;
        uploadStatus.style.color = 'var(--text-muted)';
        uploadLabel.parentElement.style.opacity = '0.5';
        uploadLabel.parentElement.style.pointerEvents = 'none';

        const formData = new FormData();
        formData.append('playlist_file', file);

        try {
            const response = await fetch(`/playlists/${currentArtistId}/upload`, { // Using global artist ID
                method: 'POST',
                body: formData,
            });

            const result = await response.json();
            if (!response.ok) throw new Error(result.error || `Server error: ${response.status}`);

            const selectedTrackName = "Playlists from Uploaded File";
            document.title = `Playlist Results for ${selectedTrackName}`;

            const resultsHtml = `
                <hr style="border-color: rgba(255,255,255,0.1); margin: 2rem 0;">
                <h2 style="text-align: center; margin-bottom: 1.5rem;">Playlist Results from "<span style="color: var(--success);">${selectedTrackName}</span>"</h2>
            `;
            const resultsContainer = document.getElementById('playlist-results-container');
            resultsContainer.innerHTML = resultsHtml;

            injectResultsAndData(
                await buildPlaylistsHtml(result.playlists),
                result.playlists
            );

            uploadStatus.textContent = `Successfully loaded ${result.playlists.length} playlists.`;

        } catch (error) {
            console.error("Upload error:", error);
            uploadStatus.textContent = `Error: ${error.message}`;
            uploadStatus.style.color = 'var(--error)';
        } finally {
            uploadLabel.parentElement.style.opacity = '1';
            uploadLabel.parentElement.style.pointerEvents = 'auto';
            fileInput.value = '';
        }
    });
}

async function buildPlaylistsHtml(playlists) {
    if (!playlists || playlists.length === 0) {
        return `<div class="card" style="text-align: center; padding: 2rem;">
                    <h3>No Playlists Loaded</h3>
                    <p style="color: var(--text-muted);">The uploaded file contained no valid playlists to display after filtering.</p>
                </div>`;
    }
    const aiFilterHtml = `
        <div style="margin-bottom: 1.5rem; max-width: 500px; margin-left: auto; margin-right: auto;">
            <label for="ai-smart-search" class="form-label" style="text-align: center;">✨ AI Smart Filter</label>
            <div class="flex gap-2">
                <input type="text" id="ai-smart-search" placeholder="e.g., chill indie pop with female vocals" class="form-input">
                <button id="ai-filter-button" type="button" class="btn btn-primary">
                    <span class="button-text">Apply</span>
                    <span class="button-loading hidden animate-spin">Applying...</span>
                </button>
            </div>
            <p id="ai-filter-status" style="font-size: 0.8rem; text-align: center; color: var(--text-muted); margin-top: 0.5rem; min-height: 1rem;"></p>
        </div>`;

    const playlistCardsHtml = playlists.map(playlist => `
        <div class="playlist-card card" style="padding: 0; overflow: hidden; display: flex; flex-direction: column;"
             data-playlist-id="${playlist.id}"
             data-found-by="${(playlist.found_by || []).map(k => k.toLowerCase()).join(',')}">
            <div style="padding: 1rem; border-bottom: 1px solid rgba(255,255,255,0.05);">
                 <a href="${playlist.url || '#'}" target="_blank" rel="noopener noreferrer" style="font-weight: 600; color: var(--primary); display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="${playlist.name || 'N/A'}">${playlist.name || 'N/A'}</a>
                 <p style="font-size: 0.8rem; color: var(--text-muted); margin-top: 0.25rem;">By: <span style="color: var(--text-main);">${playlist.owner_name || 'N/A'}</span></p>
            </div>
            <div style="padding: 1rem; flex-grow: 1; display: flex; flex-direction: column; font-size: 0.85rem;">
                ${playlist.description ? `<p style="color: var(--text-secondary); margin-bottom: 0.5rem; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;" title="${playlist.description}">${playlist.description}</p>` : ''}
                <div style="margin-top: auto; padding-top: 0.5rem; border-top: 1px solid rgba(255,255,255,0.05); display: flex; flex-direction: column; gap: 0.25rem;">
                    <div class="flex justify-between"><span style="color: var(--text-muted);">Followers:</span><span style="font-weight: 500;">${playlist.followers || 'N/A'}</span></div>
                    <div class="flex justify-between"><span style="color: var(--text-muted);">Tracks:</span><span style="font-weight: 500;">${playlist.tracks_total || 'N/A'}</span></div>
                    <div class="flex justify-between"><span style="color: var(--text-muted);">Email:</span>${playlist.email ? `<a href="mailto:${playlist.email}" style="color: var(--accent-blue);">${playlist.email}</a>` : '<span style="color: var(--text-muted); font-style: italic;">N/A</span>'}</div>
                </div>
            </div>
        </div>
    `).join('');

    return `${aiFilterHtml}
            <p id="playlist-count-display" style="text-align: center; color: var(--text-muted); font-size: 0.9rem; margin-bottom: 1rem;"></p>
            <div id="playlists-grid" class="grid gap-4" style="grid-template-columns: 1fr; max-height: 70vh; overflow-y: auto; padding-right: 0.5rem;">${playlistCardsHtml}</div>
            <p style="text-align: center; font-size: 0.8rem; color: var(--text-muted); margin-top: 1rem; font-style: italic;">(Loaded ${playlists.length} playlists from file)</p>`;
}
