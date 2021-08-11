const deviceData = {};
const webSocketMethod = window.location.protocol === "https:" ? "wss" : "ws";

function createNewGPURow(gpuData, deviceName) {
    console.log(`Create new row for GPU: ${gpuData.uuid}`);
    const gpuTemplate = $('.gpu-row-template');
    const gpuRow = gpuTemplate.clone();
    gpuRow.removeClass("gpu-row-template");
    gpuRow.addClass("gpu-row");
    gpuRow.attr("id", gpuData.uuid);
    const deviceTable = $(`#${deviceName}-gpu-table`);
    const tableBody = deviceTable.find("tbody");
    gpuRow.appendTo(tableBody);

    const processButton = gpuRow.find('.gpu-processes').find('.gpu-process-show');
    processButton.attr('data-device', deviceName);
    processButton.attr('data-gpu-uuid', gpuData.uuid);
    setupModals();
    return gpuRow;
}


function updateReservationIndicator(gpuRow, gpuIsReserved) {
    let reservationIndicator;
    if (gpuIsReserved) {
        reservationIndicator = $(".gpu-in-use-template").clone();
        reservationIndicator.removeClass("gpu-in-use-template");
    } else {
        reservationIndicator = $(".gpu-free-template").clone();
        reservationIndicator.removeClass("gpu-free-template");
    }
    reservationIndicator.addClass("gpu-reservation-indicator");
    const reservationRoot = gpuRow.find(".gpu-reservation");
    reservationRoot.empty();
    reservationIndicator.appendTo(reservationRoot);
}

function updateGPUData(data, currentUser) {
    let any_gpu_in_use = false;
    let any_gpu_failed = false;
    for (let gpu of data.gpus) {
        let gpuRow = $('#' + gpu.uuid);
        if (gpuRow.length === 0) {
            gpuRow = createNewGPURow(gpu, data.name);
        }
        gpuRow.find('.gpu-model-name').html(gpu.model_name);
        gpuRow.find('.gpu-memory').html(`${gpu.used_memory} / ${gpu.total_memory}`);
        gpuRow.find('.gpu-utilization').html(gpu.utilization);
        gpuRow.find('.gpu-last-update').timeago('init').timeago('update', new Date());

        updateReservationIndicator(gpuRow, gpu.reserved);

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


function setBorderColor(element, borderColor) {
    for (const colorName of ["success", "danger", "warning"]) {
        const className = `border-${colorName}`
        if (element.hasClass(className)) {
            element.removeClass(className);
        }
    }
    element.addClass(`border-${borderColor}`);
}

function setupWebsockets(deviceNames, currentUser) {
    for (const deviceName of deviceNames) {
        const deviceTable = $(`#${deviceName}-card`);
        const socket = new ReconnectingWebSocket(webSocketMethod + "://" + window.location.host + '/ws/device/' + deviceName + '/');
        setBorderColor(deviceTable, "warning");
        socket.device_name = deviceName;
        socket.addEventListener('open', function (event) {
            setBorderColor(deviceTable, "warning");
            console.log("Opening Socket " + socket.device_name);
        });
        socket.addEventListener('close', function (event) {
            setBorderColor(deviceTable, "danger");
            console.log("Closing socket " + socket.device_name);
            delete deviceData[socket.device_name];
        });
        socket.addEventListener('error', function (event) {
            setBorderColor(deviceTable, "danger");
            console.log("Error while opening Websocket" + event);
        });
        socket.addEventListener('message', function (event) {
            if (!deviceTable.hasClass("border-success")) {
                setBorderColor(deviceTable, "success");
            }
            console.log(`Got Message: ${event.data}`);
            const data = JSON.parse(event.data);
            deviceData[data.name] = data;
            updateGPUData(data, currentUser);
        });
     }
}

export default function(deviceNames, currentUser) {
    setupWebsockets(deviceNames, currentUser);
    setupModals();
}
