// --- State ---
var currentType = 'digits';
var currentClass = null;
var imageOffset = 0;
var imageLimit = 50;
var imageTotal = 0;
var classStats = null;

// --- Init ---
window.addEventListener('load', function() {
    loadStats();
});

function onTypeChange() {
    currentType = document.getElementById('explore-type').value;
    currentClass = null;
    imageOffset = 0;
    clearImageGrid();
    document.getElementById('image-panel-title').textContent = 'Select a class';
    document.getElementById('image-panel-page').textContent = '';
    document.getElementById('image-empty').style.display = 'block';
    document.getElementById('load-more-row').style.display = 'none';
    // Hide any tool results
    document.getElementById('mislabel-results-section').style.display = 'none';
    document.getElementById('prune-results-section').style.display = 'none';
    loadStats();
}

// --- Stats / Class List ---
async function loadStats() {
    try {
        var resp = await fetch('/api/training-data/stats');
        var data = await resp.json();
        if (!data.success) return;
        classStats = data;
        renderClassSidebar(data.ground_truth[currentType] || {});
    } catch (err) {
        console.error('Failed to load stats:', err);
    }
}

function renderClassSidebar(classCounts) {
    var sidebar = document.getElementById('class-sidebar');
    while (sidebar.firstChild) sidebar.removeChild(sidebar.firstChild);

    var keys;
    if (currentType === 'digits') {
        keys = ['0','1','2','3','4','5','6','7','8','9','NAN'];
    } else {
        keys = Object.keys(classCounts).sort(function(a, b) {
            return parseFloat(a) - parseFloat(b);
        });
    }

    keys.forEach(function(cls) {
        var count = classCounts[cls] || 0;
        var item = document.createElement('div');
        item.className = 'class-item' + (cls === currentClass ? ' active' : '');
        item.onclick = function() { selectClass(cls); };

        var nameSpan = document.createElement('span');
        nameSpan.textContent = cls;

        var countSpan = document.createElement('span');
        countSpan.className = 'class-count';
        countSpan.textContent = count;

        item.appendChild(nameSpan);
        item.appendChild(countSpan);
        sidebar.appendChild(item);
    });
}

// --- Image Grid ---
function selectClass(cls) {
    currentClass = cls;
    imageOffset = 0;
    clearImageGrid();
    document.getElementById('image-empty').style.display = 'none';

    // Update sidebar active state
    var items = document.querySelectorAll('.class-item');
    items.forEach(function(item) {
        var name = item.querySelector('span').textContent;
        if (name === cls) {
            item.classList.add('active');
        } else {
            item.classList.remove('active');
        }
    });

    loadImages();
}

async function loadImages() {
    var titleEl = document.getElementById('image-panel-title');
    titleEl.textContent = 'Loading...';

    try {
        var url = '/api/training-data/images?type=' + encodeURIComponent(currentType) +
                  '&class_name=' + encodeURIComponent(currentClass) +
                  '&offset=' + imageOffset + '&limit=' + imageLimit;

        var resp = await fetch(url);
        var data = await resp.json();

        if (!data.success) {
            titleEl.textContent = 'Error loading images';
            return;
        }

        imageTotal = data.total;
        var loaded = imageOffset + data.filenames.length;
        titleEl.textContent = 'Class "' + currentClass + '" (' + imageTotal + ' images)';
        document.getElementById('image-panel-page').textContent =
            'Showing ' + Math.min(loaded, imageTotal) + ' of ' + imageTotal;

        var grid = document.getElementById('image-grid');
        data.filenames.forEach(function(filename) {
            var img = document.createElement('img');
            img.src = '/api/training-data/image/' + encodeURIComponent(currentType) + '/' +
                      encodeURIComponent(currentClass) + '/' + encodeURIComponent(filename);
            img.alt = filename;
            img.title = filename;
            img.loading = 'lazy';
            grid.appendChild(img);
        });

        // Show/hide Load More
        var moreBtn = document.getElementById('load-more-row');
        if (loaded < imageTotal) {
            moreBtn.style.display = 'block';
        } else {
            moreBtn.style.display = 'none';
        }

        if (data.filenames.length === 0 && imageOffset === 0) {
            document.getElementById('image-empty').style.display = 'block';
            document.getElementById('image-empty').textContent = 'No images in this class.';
        }
    } catch (err) {
        titleEl.textContent = 'Network error';
    }
}

function loadMoreImages() {
    imageOffset += imageLimit;
    loadImages();
}

function clearImageGrid() {
    var grid = document.getElementById('image-grid');
    while (grid.firstChild) grid.removeChild(grid.firstChild);
}

// --- Toast ---
function showMessage(text, type) {
    var el = document.getElementById('message');
    el.textContent = text;
    el.className = 'message ' + type;
    setTimeout(function() { el.className = 'message'; }, 5000);
}
