/**
 * Table Sorting Utility
 * Provides client-side sorting functionality with localStorage persistence
 */

class TableSorter {
    constructor(tableId, options = {}) {
        this.tableId = tableId;
        this.table = document.getElementById(tableId);
        this.storageKey = options.storageKey || `table_sort_${tableId}`;
        this.defaultSort = options.defaultSort || { column: 0, direction: 'asc' };
        this.sortableColumns = options.sortableColumns || [];
        this.dataType = options.dataType || 'text'; // text, number, date
        this.currentSort = this.loadSortState();
        
        if (this.table) {
            this.init();
        }
    }

    init() {
        this.addSortHeaders();
        this.applyCurrentSort();
    }

    addSortHeaders() {
        const headers = this.table.querySelectorAll('thead th');
        headers.forEach((header, index) => {
            // Skip if column is not sortable
            if (this.sortableColumns.length > 0 && !this.sortableColumns.includes(index)) {
                return;
            }

            // Add sortable class and click handler
            header.classList.add('sortable');
            header.style.cursor = 'pointer';
            header.style.userSelect = 'none';
            
            // Add sort indicator container
            const sortIndicator = document.createElement('span');
            sortIndicator.className = 'sort-indicator';
            sortIndicator.innerHTML = '<i class="fas fa-sort text-gray-400"></i>';
            header.appendChild(sortIndicator);

            // Add click handler
            header.addEventListener('click', () => {
                this.sortByColumn(index);
            });
        });
    }

    sortByColumn(columnIndex) {
        let direction = 'asc';
        
        // If clicking the same column, toggle direction
        if (this.currentSort.column === columnIndex) {
            direction = this.currentSort.direction === 'asc' ? 'desc' : 'asc';
        }

        this.currentSort = { column: columnIndex, direction };
        this.saveSortState();
        this.applySort(columnIndex, direction);
        this.updateSortIndicators();
    }

    applySort(columnIndex, direction) {
        const tbody = this.table.querySelector('tbody');
        const rows = Array.from(tbody.querySelectorAll('tr'));

        rows.sort((a, b) => {
            const aValue = this.getCellValue(a, columnIndex);
            const bValue = this.getCellValue(b, columnIndex);

            let comparison = 0;
            
            // Handle different data types
            if (this.dataType === 'number') {
                comparison = parseFloat(aValue) - parseFloat(bValue);
            } else if (this.dataType === 'date') {
                comparison = new Date(aValue) - new Date(bValue);
            } else {
                comparison = aValue.localeCompare(bValue);
            }

            return direction === 'asc' ? comparison : -comparison;
        });

        // Re-append sorted rows
        rows.forEach(row => tbody.appendChild(row));
    }

    getCellValue(row, columnIndex) {
        const cell = row.children[columnIndex];
        if (!cell) return '';

        // Get text content, handling nested elements
        let text = cell.textContent || cell.innerText || '';
        
        // Clean up text (remove extra whitespace)
        text = text.trim();
        
        // Handle special cases
        if (text === 'N/A' || text === 'Not Applied') {
            return '';
        }

        return text;
    }

    updateSortIndicators() {
        const headers = this.table.querySelectorAll('thead th.sortable');
        headers.forEach((header, index) => {
            const indicator = header.querySelector('.sort-indicator i');
            if (index === this.currentSort.column) {
                if (this.currentSort.direction === 'asc') {
                    indicator.className = 'fas fa-sort-up text-blue-600';
                } else {
                    indicator.className = 'fas fa-sort-down text-blue-600';
                }
            } else {
                indicator.className = 'fas fa-sort text-gray-400';
            }
        });
    }

    applyCurrentSort() {
        if (this.currentSort.column !== null) {
            this.applySort(this.currentSort.column, this.currentSort.direction);
            this.updateSortIndicators();
        }
    }

    saveSortState() {
        try {
            localStorage.setItem(this.storageKey, JSON.stringify(this.currentSort));
        } catch (e) {
            console.warn('Could not save sort state to localStorage:', e);
        }
    }

    loadSortState() {
        try {
            const saved = localStorage.getItem(this.storageKey);
            if (saved) {
                return JSON.parse(saved);
            }
        } catch (e) {
            console.warn('Could not load sort state from localStorage:', e);
        }
        return this.defaultSort;
    }

    resetSort() {
        this.currentSort = this.defaultSort;
        this.saveSortState();
        this.applyCurrentSort();
    }
}

// Multi-column table sorter for complex tables
class MultiColumnTableSorter {
    constructor(tableId, columnConfigs = {}) {
        this.tableId = tableId;
        this.table = document.getElementById(tableId);
        this.storageKey = `table_sort_${tableId}`;
        this.columnConfigs = columnConfigs;
        this.currentSort = this.loadSortState();
        
        if (this.table) {
            this.init();
        }
    }

    init() {
        this.addSortHeaders();
        this.applyCurrentSort();
    }

    addSortHeaders() {
        const headers = this.table.querySelectorAll('thead th');
        headers.forEach((header, index) => {
            const config = this.columnConfigs[index];
            if (!config || !config.sortable) {
                return;
            }

            header.classList.add('sortable');
            header.style.cursor = 'pointer';
            header.style.userSelect = 'none';
            
            const sortIndicator = document.createElement('span');
            sortIndicator.className = 'sort-indicator';
            sortIndicator.innerHTML = '<i class="fas fa-sort text-gray-400"></i>';
            header.appendChild(sortIndicator);

            header.addEventListener('click', () => {
                this.sortByColumn(index);
            });
        });
    }

    sortByColumn(columnIndex) {
        const config = this.columnConfigs[columnIndex];
        if (!config) return;

        let direction = 'asc';
        
        if (this.currentSort.column === columnIndex) {
            direction = this.currentSort.direction === 'asc' ? 'desc' : 'asc';
        }

        this.currentSort = { column: columnIndex, direction };
        this.saveSortState();
        this.applySort(columnIndex, direction, config);
        this.updateSortIndicators();
    }

    applySort(columnIndex, direction, config) {
        const tbody = this.table.querySelector('tbody');
        const rows = Array.from(tbody.querySelectorAll('tr'));

        rows.sort((a, b) => {
            const aValue = this.getCellValue(a, columnIndex, config);
            const bValue = this.getCellValue(b, columnIndex, config);

            let comparison = 0;
            
            if (config.dataType === 'number') {
                comparison = parseFloat(aValue) - parseFloat(bValue);
            } else if (config.dataType === 'date') {
                comparison = new Date(aValue) - new Date(bValue);
            } else {
                comparison = aValue.localeCompare(bValue);
            }

            return direction === 'asc' ? comparison : -comparison;
        });

        rows.forEach(row => tbody.appendChild(row));
    }

    getCellValue(row, columnIndex, config) {
        const cell = row.children[columnIndex];
        if (!cell) return '';

        let text = cell.textContent || cell.innerText || '';
        text = text.trim();
        
        // Apply custom value extractor if provided
        if (config.valueExtractor) {
            return config.valueExtractor(cell, text);
        }
        
        // Handle special cases
        if (text === 'N/A' || text === 'Not Applied') {
            return '';
        }

        return text;
    }

    updateSortIndicators() {
        const headers = this.table.querySelectorAll('thead th.sortable');
        headers.forEach((header, index) => {
            const indicator = header.querySelector('.sort-indicator i');
            if (index === this.currentSort.column) {
                if (this.currentSort.direction === 'asc') {
                    indicator.className = 'fas fa-sort-up text-blue-600';
                } else {
                    indicator.className = 'fas fa-sort-down text-blue-600';
                }
            } else {
                indicator.className = 'fas fa-sort text-gray-400';
            }
        });
    }

    applyCurrentSort() {
        if (this.currentSort.column !== null) {
            const config = this.columnConfigs[this.currentSort.column];
            if (config) {
                this.applySort(this.currentSort.column, this.currentSort.direction, config);
                this.updateSortIndicators();
            }
        }
    }

    saveSortState() {
        try {
            localStorage.setItem(this.storageKey, JSON.stringify(this.currentSort));
        } catch (e) {
            console.warn('Could not save sort state to localStorage:', e);
        }
    }

    loadSortState() {
        try {
            const saved = localStorage.getItem(this.storageKey);
            if (saved) {
                return JSON.parse(saved);
            }
        } catch (e) {
            console.warn('Could not load sort state from localStorage:', e);
        }
        return { column: 0, direction: 'asc' };
    }
}

// Utility function to initialize sorting for common table types
function initTableSorting(tableId, options = {}) {
    return new TableSorter(tableId, options);
}

function initMultiColumnTableSorting(tableId, columnConfigs) {
    return new MultiColumnTableSorter(tableId, columnConfigs);
}

// Export for use in other scripts
window.TableSorter = TableSorter;
window.MultiColumnTableSorter = MultiColumnTableSorter;
window.initTableSorting = initTableSorting;
window.initMultiColumnTableSorting = initMultiColumnTableSorting;
