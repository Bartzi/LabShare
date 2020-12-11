const deviceData = {};
const webSocketMethod = window.location.protocol === "https:" ? "wss" : "ws";

function updateGPUData(data, currentUser) {
    let any_gpu_in_use = false;
    let any_gpu_failed = false;
    for (let gpu of data.gpus) {
        const gpuRow = $('#' + gpu.uuid);
        gpuRow.find('.gpu-memory').html(gpu.memory);
        gpuRow.find('.gpu-utilization').html(gpu.utilization);
        gpuRow.find('.gpu-last-update').timeago('init').timeago('update', gpu.last_update);
        gpuRow.find('.gpu-current-reservation').html(gpu.current_user);
        gpuRow.find('.gpu-next-reservation').html(gpu.next_users.length > 0 ? gpu.next_users[0] : '');

        const numGPUProcesses = gpu.processes.length;
        const processButton = gpuRow.find('.gpu-processes').find('.gpu-process-show');
        processButton.html(
            numGPUProcesses + " " + pluralize("Process", numGPUProcesses)
        );
        processButton.prop("disabled", numGPUProcesses === 0);

        if (gpu.in_use) {
            if (!gpuRow.hasClass("alert-warning")) {
                gpuRow.addClass("alert-warning");
            }
            any_gpu_in_use = true;
        } else {
            if (gpuRow.hasClass("alert-warning")) {
                gpuRow.removeClass("alert-warning");
            }
        }
        if (gpu.failed) {
            if (!gpuRow.hasClass("alert-danger")) {
                gpuRow.addClass("alert-danger");
            }
            any_gpu_failed = true;
        } else {
            if (gpuRow.hasClass("alert-danger")) {
                gpuRow.removeClass("alert-danger");
            }
        }
    }

    const deviceHeading = $('#' + data.name + '_heading');
    const deviceHeadingButton = deviceHeading.find('device-heading-btn')
    let flag, classLabel;
    for ([flag, classLabel] of [[any_gpu_in_use, "alert-warning"], [any_gpu_failed, "alert-danger"]]) {
        if (flag) {
            if (!deviceHeading.hasClass(classLabel)) {
                deviceHeading.addClass(classLabel);
                deviceHeadingButton.addClass(classLabel);
            }
        } else {
            if (deviceHeading.hasClass(classLabel)) {
                deviceHeading.removeClass(classLabel);
                deviceHeadingButton.removeClass(classLabel);
            }
        }
    }
}

function setupModals() {
    $('.gpu-process-show').on('click', function (event) {
        const that = $(this);
        const device = that.data('device');
        const uuid = that.data('gpu-uuid');
        const gpu = deviceData[device].gpus.filter(gpu => gpu.uuid === uuid)[0];
        const processes = gpu.processes;
        const gpuModal = $('#gpu-proc-list').clone().attr('id', 'full-process-list');
        const modalBody = gpuModal.find('.modal-body');
        for (const process of processes) {
            $('.gpu-process-details').clone()
                .find('.card-header').html(process.name).end()
                .find('.pid').html(process.pid).end()
                .find('.user').html(process.username).end()
                .find('.memory').html(process.memory_usage).end()
                .appendTo(modalBody);
        }
        gpuModal.modal('show');
    });

    $(document).on('hide.bs.modal', '#full-process-list', function (event) {
        $(event.target).remove();
    });
}

function setupWebsockets(deviceNames, currentUser) {
    for (const device of deviceNames) {
         const socket = new ReconnectingWebSocket(webSocketMethod + "://" + window.location.host + '/ws/device/' + device + '/');
         socket.device_name = device;
         socket.addEventListener('open', function (event) {
             console.log("Opening Socket " + socket.device_name);
         });
         socket.addEventListener('close', function (event) {
             console.log("Closing socket " + socket.device_name);
             delete deviceData[socket.device_name];
         });
         socket.addEventListener('error', function (event) {
             console.log("Error while opening Websocket" + event);
         });
         socket.addEventListener('message', function (event) {
             const data = JSON.parse(event.data);
             deviceData[socket.device_name] = data;
             updateGPUData(data, currentUser);
         });
     }
}

export default function(deviceNames, currentUser) {
    setupWebsockets(deviceNames, currentUser);
    setupModals();
}
