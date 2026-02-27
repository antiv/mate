/**
 * Shared server control functions for MATE Dashboard
 * Handles ADK server start/stop/restart operations
 */

// Server control functions
async function refreshSystemStatus() {
    try {
        const response = await fetch('/dashboard/api/server/status', {
            credentials: 'same-origin'
        });
        const status = await response.json();
        updateServerStatus(status);
    } catch (error) {
        console.error('Error checking server status:', error);
        updateServerStatus({status: 'error', message: 'Failed to check status'});
    }
}

function updateServerStatus(status) {
    // Update header status elements
    const indicator = document.getElementById('adkStatusIndicator');
    const statusText = document.getElementById('adkStatusText');
    const startBtn = document.getElementById('startAdkBtn');
    const stopBtn = document.getElementById('stopAdkBtn');
    const restartBtn = document.getElementById('restartAdkBtn');
    
    // Update overview status elements (if they exist)
    const overviewIndicator = document.getElementById('overviewAdkStatusIndicator');
    const overviewStatusText = document.getElementById('overviewAdkStatusText');
    
    // Hide all buttons first
    if (startBtn) startBtn.classList.add('hidden');
    if (stopBtn) stopBtn.classList.add('hidden');
    if (restartBtn) restartBtn.classList.add('hidden');
    
    if (status.status === 'running') {
        // Update header elements
        if (indicator) indicator.className = 'w-2 h-2 bg-green-400 rounded-full';
        if (statusText) {
            statusText.textContent = 'Online';
            statusText.className = 'text-sm text-green-600 dark:text-green-400';
        }
        if (stopBtn) stopBtn.classList.remove('hidden');
        if (restartBtn) restartBtn.classList.remove('hidden');
        
        // Update overview elements
        if (overviewIndicator) overviewIndicator.className = 'w-3 h-3 bg-green-400 rounded-full mr-3';
        if (overviewStatusText) {
            overviewStatusText.textContent = 'Online';
            overviewStatusText.className = 'text-green-600 dark:text-green-400 font-medium';
        }
    } else {
        // Update header elements
        if (indicator) indicator.className = 'w-2 h-2 bg-red-400 rounded-full';
        if (statusText) {
            statusText.textContent = 'Offline';
            statusText.className = 'text-sm text-red-600 dark:text-red-400';
        }
        if (startBtn) startBtn.classList.remove('hidden');
        
        // Update overview elements
        if (overviewIndicator) overviewIndicator.className = 'w-3 h-3 bg-red-400 rounded-full mr-3';
        if (overviewStatusText) {
            overviewStatusText.textContent = 'Offline';
            overviewStatusText.className = 'text-red-600 dark:text-red-400 font-medium';
        }
    }
}

async function controlAdkServer(action) {
    const actionBtn = document.getElementById(`${action}AdkBtn`);
    const originalText = actionBtn.innerHTML;
    
    // Show loading state
    actionBtn.disabled = true;
    actionBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>Processing...';
    
    try {
        const response = await fetch(`/dashboard/api/server/${action}`, {
            method: 'POST',
            credentials: 'same-origin'
        });
        
        const result = await response.json();
        
        if (result.success) {
            showNotification(result.message);
            // Refresh status after a brief delay
            setTimeout(refreshSystemStatus, 1000);
        } else {
            showNotification(result.message, 'error');
        }
    } catch (error) {
        showNotification(`Error ${action}ing server`, 'error');
    } finally {
        // Reset button
        actionBtn.disabled = false;
        actionBtn.innerHTML = originalText;
    }
}

// Initialize server status check when page loads
function initServerControl() {
    refreshSystemStatus();
    
    // Auto-refresh server status every 30 seconds
    setInterval(refreshSystemStatus, 30000);
}
